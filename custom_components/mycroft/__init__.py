"""The OpenAI Conversation integration."""
from __future__ import annotations

from functools import partial
from types import Optional, Tuple, List
import logging
import json
from asyncio import gather

import openai
from openai import error

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, TemplateError
from homeassistant.helpers import area_registry as ar, intent, template
from homeassistant.util import ulid

from .const import (
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    HOME_INFO_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenAI Conversation from a config entry."""
    openai.api_key = entry.data[CONF_API_KEY]

    try:
        await hass.async_add_executor_job(
            partial(openai.Engine.list, request_timeout=10)
        )
    except error.AuthenticationError as err:
        _LOGGER.error("Invalid API key: %s", err)
        return False
    except error.OpenAIError as err:
        raise ConfigEntryNotReady(err) from err

    conversation.async_set_agent(hass, entry, OpenAIAgent(hass, entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload OpenAI."""
    openai.api_key = None
    conversation.async_unset_agent(hass, entry)
    return True


class OpenAIAgent(conversation.AbstractConversationAgent):
    """OpenAI conversation agent."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.history: dict[str, str] = {}

    @property
    def attribution(self):
        """Return the attribution."""
        return {"name": "Powered by OpenAI", "url": "https://www.openai.com"}

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        new_message = {
            "role": "user",
            "content": user_input.text + " Answer in syntactically perfect json only",
        }

        # Generate conversation history
        conversation_id, messages = self.generate_conversation_history(
            user_input, new_message
        )

        # Get model and other parameters
        model = self.entry.options.get(CONF_MODEL, DEFAULT_MODEL)
        max_tokens = self.entry.options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        top_p = self.entry.options.get(CONF_TOP_P, DEFAULT_TOP_P)
        temperature = self.entry.options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)

        # Execute OpenAI API call
        result = await self.execute_openai_api_call(
            model, messages, max_tokens, top_p, temperature, conversation_id
        )

        # Parse OpenAI API response
        response = result["choices"][0]["message"]["content"]
        comment, command, error_message = self.parse_openai_response(response)

        # Handle response parsing error
        if error_message:
            return self.create_conversation_result(
                comment, conversation_id, user_input.language
            )

        # Execute commands received from the API response
        error_message = await self.execute_commands(command)
        if error_message:
            return self.create_conversation_result(
                error_message, conversation_id, user_input.language
            )

        return self.create_conversation_result(
            comment, conversation_id, user_input.language
        )

    def generate_conversation_history(
        self, user_input: conversation.ConversationInput, new_message: dict
    ) -> Tuple[str, List[dict]]:
        if user_input.conversation_id in self.history:
            conversation_id = user_input.conversation_id
            messages = self.history[conversation_id] + [new_message]
        else:
            conversation_id = ulid.ulid()
            system_prompt = self.entry.options.get(CONF_PROMPT, DEFAULT_PROMPT)
            home_info_prompt = self._async_generate_home_info_prompt(HOME_INFO_TEMPLATE)
            messages = [
                {"role": "user", "content": system_prompt},
                {"role": "assistant", "content": '{"comment":"Ok!"}'},
                {"role": "user", "content": home_info_prompt},
                {"role": "assistant", "content": '{"comment":"Got it!"}'},
                new_message,
            ]
            self.history[conversation_id] = messages

        return conversation_id, messages

    def _async_generate_home_info_prompt(self, raw_prompt: str) -> str:
        try:
            return template.Template(raw_prompt, self.hass).async_render(
                {
                    "ha_name": self.hass.config.location_name,
                    "areas": list(ar.async_get(self.hass).areas.values()),
                },
                parse_result=False,
            )
        except TemplateError as err:
            _LOGGER.error("Error rendering prompt: %s", err)
            raise

    def parse_openai_response(
        self, response: str
    ) -> Tuple[str, Optional[dict], Optional[str]]:
        try:
            if response[-2:] == ",}":
                response = response[:-2] + "}"

            response_json = json.loads(response)
            comment = response_json["comment"]
            command = response_json.get("command", None)
        except Exception as err:
            comment = f"Unable to parse: {response} \n Error: {err}"
            command = None
            error_message = str(err)

        return comment, command, error_message

    async def execute_commands(self, command: dict) -> Optional[str]:
        try:
            if type(command) == dict:
                await self.hass.services.async_call(
                    command["domain"], command["service"], command["data"]
                )
            elif type(command) == list:
                await gather(
                    *[
                        self.hass.services.async_call(
                            cmd["domain"], cmd["service"], cmd["data"]
                        )
                        for cmd in command
                    ]
                )
        except Exception as err:
            error_message = f"""Unable to execute: {command['domain'], 
                command['service'], command['data']} \n Error: {err}"""
            return error_message
        return None

    def create_conversation_result(
        self, message: str, conversation_id: str, language: str
    ) -> conversation.ConversationResult:
        intent_response = intent.IntentResponse(language=language)
        intent_response.async_set_speech(message)

        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )
