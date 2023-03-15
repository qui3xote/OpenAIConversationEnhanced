"""The OpenAI Conversation integration."""
from __future__ import annotations

from functools import partial
import logging
import json

import openai
from openai import error

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, TemplateError
from homeassistant.helpers import area_registry as ar, intent, service, template
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
        """Process a sentence."""
        system_prompt = self.entry.options.get(CONF_PROMPT, DEFAULT_PROMPT)
        model = self.entry.options.get(CONF_MODEL, DEFAULT_MODEL)
        max_tokens = self.entry.options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        top_p = self.entry.options.get(CONF_TOP_P, DEFAULT_TOP_P)
        temperature = self.entry.options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        new_message = {
            "role": "user",
            "content": user_input.text
            + " Answer in syntactially perfect json and only json,",
        }

        if user_input.conversation_id in self.history:
            conversation_id = user_input.conversation_id
            messages = self.history[conversation_id] + [new_message]
        else:
            try:
                home_info_prompt = self._async_generate_prompt(HOME_INFO_TEMPLATE)
            except TemplateError as err:
                _LOGGER.error("Error rendering prompt: %s", err)
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_error(
                    intent.IntentResponseErrorCode.UNKNOWN,
                    f"Sorry, I had a problem with my template: {err}",
                )
                return conversation.ConversationResult(
                    response=intent_response, conversation_id=conversation_id
                )

            conversation_id = ulid.ulid()
            _LOGGER.info("System Prompt: {system_prompt}")
            _LOGGER.info("Home Info: {home_info_prompt}")
            messages = [
                {"role": "user", "content": system_prompt},
                {"role": "assistant", "content": '{"comment":"Ok!"}'},
                {"role": "user", "content": home_info_prompt},
                {"role": "assistant", "content": '{"comment":"Got it!"}'},
                new_message,
            ]

        user_name = "User"
        if (
            user_input.context.user_id
            and (
                user := await self.hass.auth.async_get_user(user_input.context.user_id)
            )
            and user.name
        ):
            user_name = user.name

        # prompt += f"\n{user_name}: {user_input.text}\nSmart home: "

        # _LOGGER.info("Prompt for %s: %s", model, prompt)

        try:
            result = await openai.ChatCompletion.acreate(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                top_p=top_p,
                temperature=temperature,
                user=conversation_id,
            )
        except error.OpenAIError as err:
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem talking to OpenAI: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        _LOGGER.info("Response %s", result)
        response = result["choices"][0]["message"]["content"]
        self.history[conversation_id] = messages + [
            {"role": "assistant", "content": response}
        ]

        try:
            if response[-2:] == ",}":
                response = response[-2:] + "}"

            response_json = json.loads(response)
            comment = response_json["comment"]
        except Exception as err:
            comment = f"Unable to parse: {response} \n Error: {err}"
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(comment)

            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        try:
            if (
                "command" in response_json.keys()
                and type(response_json["command"]) == dict
            ):
                await self.hass.services.async_call(
                    response_json["command"]["domain"],
                    response_json["command"]["service"],
                    response_json["command"]["data"],
                )
        except Exception as err:
            comment = f"""Unable to execute: {response_json["command"]['domain'], 
                    response_json["command"]['service'],  
                   response_json["command"]['data']} \n Error: {err}"""
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(comment)

            return conversation.ConversationResult(
                response=intent_response, conversation_id=conversation_id
            )

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(comment)

        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    def _async_generate_prompt(self, raw_prompt: str) -> str:
        """Generate a prompt for the user."""
        return template.Template(raw_prompt, self.hass).async_render(
            {
                "ha_name": self.hass.config.location_name,
                "areas": list(ar.async_get(self.hass).areas.values()),
            },
            parse_result=False,
        )
