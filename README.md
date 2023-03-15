# OpenAIConversationEnhanced
An implementation of ChatGPT as a conversation agent that can actually control your home.


## Installation
For now, add this as a custom repository to HACS, reboot your machine and then add it through the gui integration config flow. The first dialog asks for your OpenAI api key (though it may not say that - I have no idea why it's just a blank box with a text input) which you can obtain from OpenAI. After that, it should work. You may (probably) need to uninstall the official OpenAI conversation agent in order for it to work. 

## How does it work?
Mostly by instructing ChatGPT to reply in json format, including a 'comment' which is what gets relayed back to the user, and a "command" which is a home assistant service call. 

## Limitations, Caveats and warnings:

1. It definitely works on various requests I've given it, but.... YMMV and I have limited time to work on this, so I make zero promises about when/if I can help you out. 
2. Your data (include a fair amount of data about entities in your home) is being pumped over the internet. I'm not worried by what is exposed (and it is over a secure connection) - if a hacker can do something useful with the entities created by home-assistant, they already have a lot of access to my home. But you should be aware and make a thoughtful decision. 
3. ChatGPT is limited to around 4k tokens (~3k words) per session. Passing all those entities and their current states chews up a fair number of tokens, depending on how much HA stuff you have. You may hit that limit after only a few requests. Shutting the chat window and refreshing your browser starts a new ssession. (there are probably smarter ways to deal with this, I might improve it later if I feel like it and have time).
4. It's slower than I'd like - not sure if there is anything I can do on my end to fix that (probably a little). 
