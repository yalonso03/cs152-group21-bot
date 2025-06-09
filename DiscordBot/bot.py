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
import heapq
import itertools
from openai import OpenAI
from dotenv import load_dotenv
from discord import Embed

load_dotenv()

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


NEED_SUICIDE_HOTLINE = "need_suicide_hotline"
NEED_911 = "need_911"
NEED_MENTAL_HEALTH_HOTLINE = "need_mental_health_hotline"
NEED_NONCON_PORNOGRAPHY_HOTLINE = "need_nonconsensual_pornography_hotline"
NEED_FINANCIALLY_MOTIVATED_SEXTORTION_HELP = "need_financially_motivated_sextortion_help"


LLM_PROMPT = """You are an assistant helping detect cases of sextortion on Discord. 

Sextortion is defined as the practice of extorting money or sexual favors from someone by threatening to reveal evidence of their sexual activity. You will be given a message or conversation, and your task is to analyze whether it includes signs of sextortion or related risks.

Please respond ONLY with a JSON object in the following format:

{
  "should_flag_for_mod_review": False,
  "contains_sextortion": False,
  "resources_needed": {
    "need_suicide_hotline": False,
    "need_911": False,
    "need_mental_health_hotline": False,
    "need_nonconsensual_pornography_hotline" : False,
    "need_financially_motivated_sextortion_help" : False
  }
}

Update the values (`true` or `false`) based on the content you analyze.

Now analyze the following content:
"""


RESOURCES_DICT = {
    NEED_NONCON_PORNOGRAPHY_HOTLINE : {
        "url" : "https://cybercivilrights.org/ccri-crisis-helpline/",
        "text" : "The CCRI Crisis Hotline for Nonconsensual Pornography"
    },
    NEED_911 : {
        "url" : "https://www.911.gov/",
        "text" : "911 for Emergencies"
    },
    NEED_MENTAL_HEALTH_HOTLINE : {
        "url" : "https://mentalhealthhotline.org/",
        "text" : "National Mental Health Hotline"
    },
    NEED_SUICIDE_HOTLINE : {
        "url" : "https://988lifeline.org/chat/",
        "text" : "Suicide Crisis Hotline"
    },
    NEED_FINANCIALLY_MOTIVATED_SEXTORTION_HELP : {
        "url" : "https://www.fbi.gov/how-we-can-help-you/scams-and-safety/common-frauds-and-scams/sextortion/financially-motivated-sextortion",
        "text" : "Reporting Financially Motivated Sextortion to the FBI"
    }

}

# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)
#client = OpenAI()  # added for milestone 3


# There should be a file called 'tokens.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens['discord']


async def fetch_message_object_from_url(bot: discord.Client, jump_url: str) -> discord.Message:
        """
        Given a Discord message URL, fetch and return the corresponding discord.Message object.
        """

        match = re.search(r"/channels/(\d+)/(\d+)/(\d+)", jump_url)
        if not match:
            raise ValueError(f"Invalid Discord message URL format: {jump_url}")

        guild_id = int(match.group(1))
        channel_id = int(match.group(2))
        message_id = int(match.group(3))

        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)

        return await channel.fetch_message(message_id)



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
        #self.report_queue = deque() #first in first out queue of completed reports
        self.report_queue = []
        heapq.heapify(self.report_queue)# Yasmine changed this to a heapq so we could do priority queue
        self.counter = itertools.count()   # for timestamp
        self.potentially_contain_sextortion_codes = ["4b", "4d", "5a", "5b", "5c", "6d"]  # values in the OPTIONS dict in report.py that should be prioritized
        # priority values 
        
        self.SEXTORTION_PRIORITY = 1  # for reports that potentially have sextortion
        self.OTHER_PRIORITY = 2       # for reports that likely were not because of sextortion
        

    def process_message(self, message_string):
        """
        This function queries OPEN AI
        """

        response = openai_client.chat.completions.create(
            model="gpt-4-1106-preview",  # or "gpt-4o" if you prefer
            messages=[
                {"role": "system", "content": "Respond only with a JSON object like: {\"answer\": true} or {\"answer\": false}."},
                {"role": "user", "content": LLM_PROMPT + message_string}
            ],
            temperature=0,
            max_tokens=100,
            #response_format="json"  # ensures structured JSON response
            response_format={"type": "json_object"}
        )
       
        """
        {
            "should_flag_for_mod_review": false,
            "contains_sextortion": false,
            "resources_needed": {
                "need_suicide_hotline": false,
                "need_911": false,
                "need_mental_health_hotline": false
            }
        }

        """
        # 

        #output_json = response.output_text
        evaluation_json = response.choices[0].message.content
        evaluation_dict = json.loads(evaluation_json)
        

        return evaluation_dict


    async def block_user(self, blocker, offender):    #j
        self.blocklists.setdefault(blocker.id, set()).add(offender.id)

    #for enqueing a report for mod review
    #meta keys: reporter, offender, jump_url, category_code, label
    async def enqueue_report(self, meta: dict):
        #adds the report metadata dictionary to the queue.

        # Case where this was an automated report -- gets handled a little differently than a manually reported one.
        if "automated_report" in meta:
            # IF the report was generated automatically 
            eval_json = meta["evaluation_json"]

            priority_val = self.SEXTORTION_PRIORITY if eval_json["contains_sextortion"] else self.OTHER_PRIORITY

            #ENQUEUE REPORT
            heapq.heappush(self.report_queue, (priority_val, next(self.counter), meta))
            #edge case where the offender is a raw User (not a Member) and has no guild attribute.
            if hasattr(meta['offender'], "guild") and meta['offender'].guild:
                guild_id = meta['offender'].guild.id
            else:
                guild_id = next(iter(self.mod_channels.keys()))  # fall back to first guild
            #get the mod channel from server
            mod_channel = self.mod_channels[guild_id]
            #sends a notification to the mod channel with the report's summary.

            reporter_mention = (
                meta["reporter"].mention if "reporter" in meta and hasattr(meta["reporter"], "mention")
                else "Automated Bot"
            )
            offender_mention = (
                meta["offender"].mention if hasattr(meta["offender"], "mention")
                else str(meta["offender"])
            )
            
            await mod_channel.send(
                f"New **AUTOMATED Queued report**\n"
                f"Reporter: {reporter_mention} | Offender: {offender_mention}\n{meta['jump_url']}"
)           
            

        else:
            # If the category code that the user selected is one of the ones that we've flagged for likely being 
            # related to sextortion, we're gonna push it with priority SEXTORTION_PRIORITY to prioritize it
            priority_val = self.SEXTORTION_PRIORITY if meta['category_code'] in self.potentially_contain_sextortion_codes else self.OTHER_PRIORITY
            heapq.heappush(self.report_queue, (priority_val, next(self.counter), meta))

            #edge case where the offender is a raw User (not a Member) and has no guild attribute.
            if hasattr(meta['offender'], "guild") and meta['offender'].guild:
                guild_id = meta['offender'].guild.id
            else:
                guild_id = next(iter(self.mod_channels.keys()))  # fall back to first guild
            #get the mod channel from server
            mod_channel = self.mod_channels[guild_id]
            #sends a notification to the mod channel with the report's summary.
        
            reporter_mention = (
                meta["reporter"].mention if "reporter" in meta and hasattr(meta["reporter"], "mention")
                else "Automated Bot"
            )
            offender_mention = (
                meta["offender"].mention if hasattr(meta["offender"], "mention")
                else str(meta["offender"])
)           

            await mod_channel.send(
                f"New **Queued report** {meta['category_code']} — {meta['label']}\n"
                f"Reporter: {reporter_mention} | Offender: {offender_mention}\n{meta['jump_url']}"
            )
            

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search(r'[gG]roup (\d+) [bB]ot', self.user.name)

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

        # -------------------
        # Automated detection portion

        # This is the format of the evaluation json dictionary
        """
        {
            "should_flag_for_mod_review": false,
            "contains_sextortion": false,
            "resources_needed": {
                "need_suicide_hotline": false,
                "need_911": false,
                "need_mental_health_hotline": false
            }
        }
        """
        # Evaluate the message with OpenAI API call
        # print("calling process message from handle channel message")
        evaluation_json = self.process_message(message.content)
    
        
        # If API call determined that this should be flagged for mod review, enqueue it automatically
        if evaluation_json["should_flag_for_mod_review"]:
            # print("in if statement -- message should be flagged for mod review")

            report_dict = {
                "automated_report": True,
                "evaluation_json": evaluation_json,
                "reporter": None,
                "offender": message.author,
                "jump_url": message.jump_url,
                "category_code": "n/a",
                "label": "Automated Report"
            }
            await self.enqueue_report(report_dict)

            # Then provide the necessary resources
            resources_dict = evaluation_json["resources_needed"]
    
            
            n_resources_needed = sum([1 if resources_dict[pot_resource] else 0 for pot_resource in resources_dict.keys()])
            if n_resources_needed > 0:  # if there's at least one resource that needs to be given
                # Send a message in the channel indicating that harm might be caused
                await message.channel.send("**This message was flagged as likely causing harm. It will be reviewed by a moderator and dealt with ASAP. In the meantime, please feel free to reference the below resources.**")
                for pot_resource in resources_dict.keys():
                    if resources_dict[pot_resource]:
                        # then need to provide this resource
                        res = RESOURCES_DICT[pot_resource]
                        embed = Embed(
                            title=res["text"],
                            url=res["url"],
                            description="Please click to access this resource.",
                            color=0xff5555  # red-ish
                        )
                        await message.channel.send(embed=embed)




        """
        this is what the report dict shoudl look like 
        {
                "reporter":      self.reporter, #captures who submitted the report
                "offender":      self.message.author, #captures the identity of the person who sent reported msg
                "jump_url":      self.message.jump_url, #has link to msg that was reported
                "category_code": self.category_code, #gives info about the category and subcategory chosen
                "label":         self.label_for_code(self.category_code) #generate a label for code
            }

        """
    

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')
        scores = self.eval_text(message.content)
        await mod_channel.send(self.code_format(scores))

    # NEW VERSION!!! yasmine added for the moderator to moderate
    # literally just tracess thru the flowchart aili made. 
    async def moderate_reports_flow(self, mod_channel):
        def check(m):
            return m.channel == mod_channel and m.author != self.user
        goto_end = False
        while self.report_queue:
            priority, time, report = heapq.heappop(self.report_queue)

            reporter = report.get("reporter")  # could be None
            reporter_name = reporter.name if reporter and hasattr(reporter, "name") else "Automated Bot"


            offender = report["offender"]
            message = report["jump_url"]
            category_code = report["category_code"]
            label = report["label"]


            report_summary = (
                f"Reviewing report by: {reporter_name}\n"
                f"Against: {offender.name}\n"
                f"Reason: {category_code} - {label}\n"
                f"Message info: {message}\n"
            )

            moderator_notes = {
                "offender": offender.name,
                "reporter": reporter.name if reporter and hasattr(reporter, "name") else "Automated Bot",  # REPLACED
                "message_url": message,
                "violates_guidelines": "no",
                "reason": None,
                "message_taken_down": False,
                "offender_action": "none",
                "report_sent": False,
                "report_note": None
            }

            await mod_channel.send(
                report_summary + "\nIs this message in violation of any of our community guidelines? (yes/no)"
            )

            try:
                response = await self.wait_for("message", check=check, timeout=60.0)
            except asyncio.TimeoutError:
                await mod_channel.send("No response. Returning report to queue.")
                #self.report_queue.append(report)
                priority_val = self.SEXTORTION_PRIORITY if report['category_code'] in self.potentially_contain_sextortion_codes else self.OTHER_PRIORITY
                #heapq.heappush(self.report_queue, (priority_val, report))
                heapq.heappush(self.report_queue, (priority_val, next(self.counter), report))
                continue

            if response.content.lower() != "yes":
                await mod_channel.send("Marking report as resolved.")
            else:
                moderator_notes["violates_guidelines"] = "yes"
                await mod_channel.send("Is the message threatening to share/sharing nude images? (yes/no)")
                try:
                    nude_response = await self.wait_for("message", check=check, timeout=60.0)
                except asyncio.TimeoutError:
                    await mod_channel.send("No response. Returning report to queue.")
                    self.report_queue.append(report)
                    continue

                if nude_response.content.lower() == "yes":
                    await mod_channel.send("Is the message in violation of Federal laws? (yes/no)")
                    try:
                        fed_response = await self.wait_for("message", check=check, timeout=60.0)
                    except asyncio.TimeoutError:
                        await mod_channel.send("No response. Returning report to queue.")
                        #self.report_queue.append(report)
                        priority_val = self.SEXTORTION_PRIORITY if report['category_code'] in self.potentially_contain_sextortion_codes else self.OTHER_PRIORITY
                        #heapq.heappush(self.report_queue, (priority_val, report))
                        heapq.heappush(self.report_queue, (priority_val, next(self.counter), report))
                        continue

                    if fed_response.content.lower() == "yes":
                        await mod_channel.send("This will send a report to the authorities. If you would like to proceed with submitting a report, reply yes (and you will be further prompted to submit information for the report). If you would not like to proceed, please reply cancel. (yes/cancel)")
                        try:
                            auth_response = await self.wait_for("message", check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            await mod_channel.send("No response. Returning report to queue.")
                            # self.report_queue.append(report)
                            priority_val = self.SEXTORTION_PRIORITY if report['category_code'] in self.potentially_contain_sextortion_codes else self.OTHER_PRIORITY
                            #heapq.heappush(self.report_queue, (priority_val, report))
                            heapq.heappush(self.report_queue, (priority_val, next(self.counter), report))
                            continue

                        if auth_response.content.lower() == "yes":
                            await mod_channel.send("Please enter further information to be included in the report to authorities:")
                            try:
                                report_note_msg = await self.wait_for("message", check=check, timeout=60.0)
                                moderator_notes["report_note"] = report_note_msg.content
                            except asyncio.TimeoutError:
                                moderator_notes["report_note"] = "No additional comment provided."

                            await mod_channel.send("The report has been sent to the authorities (simulated).")
                            moderator_notes["report_sent"] = True
                            try:

                                # delete the message by getting the message object
                                message_obj = await fetch_message_object_from_url(self, message)
                                await message_obj.delete()
                                await offender.send(f"Your message has been taken down: {message_obj.content}")


                                await offender.send("You have been banned.")
                                moderator_notes["message_taken_down"] = True
                                moderator_notes["offender_action"] = "banned"
                            except:
                                await mod_channel.send(f"Failed to DM {offender.name}.")
                            goto_end = True
                        else:
                            goto_end = await self._takedown_flow(mod_channel, offender, message, check, moderator_notes)
                    else:
                        goto_end = await self._takedown_flow(mod_channel, offender, message, check, moderator_notes)

                else:
                    await mod_channel.send("Which community guideline was violated? Enter letter or 'none':\n"
                                        "a) Promoting Illegal Items\n"
                                        "b) Spam/Fraud\n"
                                        "c) Harassment/Hate\n"
                                        "d) Nudity/Sexual Activity\n"
                                        "e) Violence/Threats\n"
                                        "f) Other\n")
                    try:
                        reason_msg = await self.wait_for("message", check=check, timeout=60.0)
                        if reason_msg.content.lower() == "none":
                            goto_end = True
                        elif reason_msg.content.lower() == "f":
                            await mod_channel.send("Enter custom explanation:")
                            custom_msg = await self.wait_for("message", check=check, timeout=60.0)
                            reason_text = custom_msg.content
                        else:
                            reasons = {
                                "a": "Promoting Illegal Items",
                                "b": "Spam/Fraud",
                                "c": "Harassment/Hate",
                                "d": "Nudity/Sexual Activity",
                                "e": "Violence/Threats",
                            }
                            reason_text = reasons.get(reason_msg.content.lower(), "Unknown reason")
                        moderator_notes["reason"] = reason_text

                        await mod_channel.send("Is the message in violation of Federal laws? (yes/no)")
                        fed2_msg = await self.wait_for("message", check=check, timeout=60.0)
                        if fed2_msg.content.lower() == "yes":
                            await mod_channel.send("This will send a report to the authorities. If you would like to proceed, please add a moderator comment to the report and reply yes. If you would not like to proceed, please reply cancel. (yes/cancel)")
                            auth2_msg = await self.wait_for("message", check=check, timeout=60.0)
                            if auth2_msg.content.lower() == "yes":
                                await mod_channel.send("Please enter further information to be included in the report to authorities:")
                                try:
                                    report_note_msg = await self.wait_for("message", check=check, timeout=60.0)
                                    moderator_notes["report_note"] = report_note_msg.content
                                except asyncio.TimeoutError:
                                    moderator_notes["report_note"] = "No additional comment provided."

                                await mod_channel.send("The report has been sent to the authorities (simulated).")
                                moderator_notes["report_sent"] = True
                                try:
                                    # delete the message by getting the message object
                                
                                    message_obj = await fetch_message_object_from_url(self, message)
                                    await message_obj.delete()
                                    await offender.send(f"Your message has been taken down: {message_obj.content}")
                    


                                    await offender.send("You have been banned.")
                                    moderator_notes["message_taken_down"] = True
                                    moderator_notes["offender_action"] = "banned"
                                except:
                                    await mod_channel.send(f"Failed to DM {offender.name}.")
                                goto_end = True
                            else:
                                goto_end = await self._takedown_flow(mod_channel, offender, message, check, moderator_notes)
                        else:
                            goto_end = await self._takedown_flow(mod_channel, offender, message, check, moderator_notes)
                    except asyncio.TimeoutError:
                        await mod_channel.send("No response. Returning report to queue.")
                        #self.report_queue.append(report)
                        priority_val = self.SEXTORTION_PRIORITY if report['category_code'] in self.potentially_contain_sextortion_codes else self.OTHER_PRIORITY
                        #heapq.heappush(self.report_queue, (priority_val, report))
                        heapq.heappush(self.report_queue, (priority_val, next(self.counter), report))
                        continue

            if not goto_end:
                continue

            summary = (
                f"Finished moderating! Here is a summary of how you handled this report:\n"
                f"Message from: {moderator_notes['offender']} to: {moderator_notes['reporter']}\n"
                f"Violates community guidelines: {moderator_notes['violates_guidelines']}\n"
                f"Reason: {moderator_notes['reason']}\n"
                f"Message taken down: {'yes' if moderator_notes['message_taken_down'] else 'no'}\n"
                f"Action taken: {moderator_notes['offender_action']}\n"
                f"Report sent to authorities: {'yes' if moderator_notes['report_sent'] else 'no'}\n"
                f"Moderator comments on report: {moderator_notes['report_note']}\n"
            )
            await mod_channel.send(summary)

            ####
            # This is for sending another response back to the user who submitted a manual report
            if reporter and reporter.name != "Automated Bot":
                try:
                    dm_summary = (
                        f"**Thank you for your report against {moderator_notes['offender']} earlier.**\n"
                        f"We're following up to notify you of how we proceeded with the report:\n"
                        f"      ----------------------------------------------------------------------------------------\n"
                        f"      Violates community guidelines: {moderator_notes['violates_guidelines']}\n"
                        f"      Reason: {moderator_notes['reason'] or 'N/A'}\n"
                        f"      Message taken down: {'yes' if moderator_notes['message_taken_down'] else 'no'}\n"
                        f"      Action taken: {moderator_notes['offender_action']}\n"
                        f"      Report sent to authorities: {'yes' if moderator_notes['report_sent'] else 'no'}\n"
                        f"      ----------------------------------------------------------------------------------------\n"
                        f"Again, we thank you for helping keep our community a safe place."
                    )
                    await reporter.send(dm_summary)
                except:
                    await mod_channel.send(f"Could not DM {reporter.name} about the outcome.")


            ####

            await mod_channel.send("Continue moderating more reports? (yes/no)")
            try:
                cont_msg = await self.wait_for("message", check=check, timeout=60.0)
                if cont_msg.content.lower() != "yes":
                    break
            except asyncio.TimeoutError:
                await mod_channel.send("No response. Ending session.")
                break

        await mod_channel.send("All done moderating! Thank you for helping keep our community safe.")


    async def _takedown_flow(self, mod_channel, offender, message, check, moderator_notes):
        try:
            message_obj = await fetch_message_object_from_url(self, message)
            await message_obj.delete()
            await offender.send(f"Your message has been taken down: {message_obj.content}")
            moderator_notes["message_taken_down"] = True
        except:
            await mod_channel.send(f"Failed to DM {offender.name} about takedown.")

        await mod_channel.send("What actions should we take against this user?\n"
                            "a) Permanently ban\n"
                            "b) Suspend for 1 week\n"
                            "c) Mute account for 1 day\n"
                            "d) No further action")
        try:
            action_msg = await self.wait_for("message", check=check, timeout=60.0)
            action = action_msg.content.lower()
            if action == "a":
                await offender.send("You have been permanently banned.")
                moderator_notes["offender_action"] = "banned"
            elif action == "b":
                await offender.send("Your account has been suspended for one week.")
                moderator_notes["offender_action"] = "suspended"
            elif action == "c":
                await offender.send("Your account has been muted for one day.")
                moderator_notes["offender_action"] = "muted"
            elif action == "d":
                moderator_notes["offender_action"] = "none"
        except asyncio.TimeoutError:
            await mod_channel.send("No action selected. Proceeding.")

        return True



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