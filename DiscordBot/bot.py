# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import Report
import pdb
from collections import deque
import asyncio

# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# There should be a file called 'tokens.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens['discord']


class ModBot(discord.Client):
    def __init__(self): 
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.reports = {} # Map from user IDs to the state of their report

        #! JOEL ADDED HERE
        self.blocklists = {} #create a dict key is blocker id and val is set of offender ids
        self.report_queue = deque() #first in first out queue of completed reports
    #record blocker does not want to see the offender, aka adds to blocklists dict


    async def block_user(self, blocker, offender):    #j
        self.blocklists.setdefault(blocker.id, set()).add(offender.id)

    #for enqueing a report for mod review
    #meta keys: reporter, offender, jump_url, category_code, label
    async def enqueue_report(self, meta: dict):
        #adds the report metadata dictionary to the queue.
        self.report_queue.append(meta)
        #edge case where the offender is a raw User (not a Member) and has no guild attribute.
        if hasattr(meta['offender'], "guild") and meta['offender'].guild:
            guild_id = meta['offender'].guild.id
        else:
            guild_id = next(iter(self.mod_channels.keys()))  # fall back to first guild
        #get the mod channel from server
        mod_channel = self.mod_channels[guild_id]
        #sends a notification to the mod channel with the report's summary.
        await mod_channel.send(
            f"New **Queued report** {meta['category_code']} — {meta['label']}\n"
            f"Reporter: {meta['reporter'].mention} | "
            f"Offender: {meta['offender'].mention}\n{meta['jump_url']}"
        )
    #! JOEL ADDED end 

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
        if match:
            self.group_num = match.group(1)
        else:
            raise Exception("Group number not found in bot's name. Name format should be \"Group # Bot\".")

        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f'group-{self.group_num}-mod':
                    self.mod_channels[guild.id] = channel
        

    async def on_message(self, message):
        '''
        This function is called whenever a message is sent in a channel that the bot can see (including DMs). 
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel. 
        '''
        # Ignore messages from the bot 
        if message.author.id == self.user.id:
            return

        
        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        # Handle a help message
        if message.content == Report.HELP_KEYWORD:
            reply =  "Use the `report` command to begin the reporting process.\n"
            reply += "Use the `cancel` command to cancel the report process.\n"
            await message.channel.send(reply)
            return

        author_id = message.author.id
        responses = []

        # Only respond to messages if they're part of a reporting flow
        if author_id not in self.reports and not message.content.startswith(Report.START_KEYWORD):
            return

        # If we don't currently have an active report for this user, add one
        if author_id not in self.reports:
            self.reports[author_id] = Report(self)

        # Let the report class handle this message; forward all the messages it returns to uss
        responses = await self.reports[author_id].handle_message(message)
        for r in responses:
            await message.channel.send(r)

        # If the report is complete or cancelled, remove it from our map
        if self.reports[author_id].report_complete():
            self.reports.pop(author_id)

    async def handle_channel_message(self, message):

        # Yasmine added to detect when a moderator sends the word "moderate" to the channel to begin the
        # mod reporting flow.
        mod_channel = self.mod_channels.get(message.guild.id)
        if (mod_channel and message.channel.id == mod_channel.id and message.content.strip().lower() == "moderate"
        ):
            await self.moderate_reports_flow(mod_channel)
            return

        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}':
            return
        #hides messages from user that is blocked
        for offenders in self.blocklists.values():
            if message.author.id in offenders:
                return
        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')
        scores = self.eval_text(message.content)
        await mod_channel.send(self.code_format(scores))

    # Yasmine added, this gets called when a moderator wants to begin moderating
    # keys for the report dict are: reporter, offender, jump_url, category_code, label
    async def moderate_reports_flow(self, mod_channel):
        while self.report_queue:
            report = self.report_queue.popleft() #dequeue from q the next report to moderate

            # info from the report in the queue 
            reporter = report["reporter"]
            offender = report["offender"]
            message = report["jump_url"]
            category_code = report["category_code"]
            label = report["label"]

            # print this out 
            report_summary = f"Reviewing report by: {reporter.name}\nAgainst: {offender.name}\nReason: {category_code} - {label}\nMessage info: {message}\n\n"

            # First see if the message should be taken down (idk if this is possible to do, it currently just simulates this by sending the user a DM)
            await mod_channel.send(
                report_summary +
                f'Would you like to take down this message? Please enter yes/no.'
            )
            def check(m): return m.channel == mod_channel and m.author != self.user
            try:
                response = await self.wait_for("message", check=check, timeout=60.0)
            except asyncio.TimeoutError:
                await mod_channel.send("No response received from moderator. Adding report back into queue and continuing on.")
                self.report_queue.append(report)
                continue

            if response.content.lower() != "yes":
                await mod_channel.send("Continuing to the next report...")
                continue

            # have moderator give reason for why message needs to be taken down
            # these are just the reasons from Aili's flowchart but easy to tweak
            reasons = {
                "a": "Promoting Illegal Items",
                "b": "Spam/Fraud",
                "c": "Harassment/Hate",
                "d": "Nudity/Sexual Activity",
                "e": "Violence/Threats",
                "f": "Other"
            }
            reasons_str = "\n".join([f"{k}) {v}" for k, v in reasons.items()])
            await mod_channel.send(
                f"Which reason applies? Please enter a letter:\n{reasons_str}"
            )
            try:
                reason_msg = await self.wait_for("message", check=check, timeout=60.0)
                reason_code = reason_msg.content.lower()
                if reason_code == "f":
                    await mod_channel.send("Please enter a custom explanation:")
                    custom_msg = await self.wait_for("message", check=check, timeout=60.0)
                    reason_text = custom_msg.content
                else:
                    reason_text = reasons.get(reason_code, "No reason provided.")
            except asyncio.TimeoutError:
                await mod_channel.send("No response received. Adding report back to queue and moving to next report.")
                self.report_queue.append(report)
                continue
            try:
                
                await offender.send(  # tell the user that their message was taken down (simulate removal, idk if it's possible)
                    f"Your message was taken down for the following reason:\n{reason_text}"
                )
            except discord.Forbidden:
                await mod_channel.send(f"Could not successfully DM {offender.name}.")
            except Exception as e:
                await mod_channel.send(f"An error occurred while DMing {offender.name}: {str(e)}")

            # Step 4: Ask how to deal with the user
            actions = {
                "a": "Ban the user",
                "b": "Suspend the user",
                "c": "Continue the conversation",
                "d": "Do nothing"
            }
            actions_str = "\n".join([f"{k}) {v}" for k, v in actions.items()])
            await mod_channel.send(
                f"How would you like to proceed in dealing with {offender.name}?\n{actions_str}"
            )
            try:
                action_msg = await self.wait_for("message", check=check, timeout=60.0)
                action_code = action_msg.content.lower()
                if action_code == "a":
                    await offender.send("You have been banned for violating our community guidelines.")
                elif action_code == "b":
                    await mod_channel.send("Enter the number of weeks that the user should be suspended for:")
                    duration_msg = await self.wait_for("message", check=check, timeout=60.0)
                    try:
                        duration = int(duration_msg.content)
                        # tell the user they're suspended for x weeks (mimic suspension w a message)
                        await offender.send(f"You are suspended for {duration} week(s) due to a violation.")
                        await mod_channel.send(f"Suspended {offender.name} for {duration} weeks.")
                    except ValueError:
                        await mod_channel.send("Invalid duration. Skipping suspension.")
                elif action_code == "c":
                    await mod_channel.send("Please DM the reporter to get the necessary information.")
                elif action_code == "d":
                    await mod_channel.send("No action taken.")
            except asyncio.TimeoutError:
                await mod_channel.send("No response received. Adding user's report back into queue and moving to next report.")
                continue

            # give mod an option to stop reviewing reports
            await mod_channel.send("Would you like to continue moderating more reports? Respond with continue/stop.")
            try:
                cont_msg = await self.wait_for("message", check=check, timeout=60.0)
                if cont_msg.content.lower() == "stop":
                    await mod_channel.send("Moderation session ended.")
                    return
            except asyncio.TimeoutError:
                await mod_channel.send("No response received. Ending session.")
                return
        await mod_channel.send("No more user reports to moderate.")



    def eval_text(self, message):
        ''''
        TODO: Once you know how you want to evaluate messages in your channel, 
        insert your code here! This will primarily be used in Milestone 3. 
        '''
        return message

    
    def code_format(self, text):
        ''''
        TODO: Once you know how you want to show that a message has been 
        evaluated, insert your code here for formatting the string to be 
        shown in the mod channel. 
        '''
        return "Evaluated: '" + text+ "'"


client = ModBot()
client.run(discord_token)