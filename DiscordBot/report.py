#In this file using the starter code I will aggregate onto this and edit it such that the
#user reporting flow is implemented
from enum import Enum, auto
import discord
import re

#make a category tree that has all the options a user can choose from
#and include the children of each parent

# category codes that potentially contain sextortion: 4b 4d 5a 5b 5c 6d
OPTIONS_TREE = {
    #first in dict is if user does not like post
    "1": {"label": "I don’t like it", "children": {}},
    #second shoice from flow
    "2": {"label": "Promoting Illegal Items",
          "children": {"2a": "Drugs", "2b": "Weapons", "2c": "Animals"}},
    #third choice if user is reporting fraud or spam
    "3": {"label": "Spam or Fraud",
          "children": {"3a": "Spam", "3b": "Copyright Infringement",
                       "3c": "Fraud or Scam", "3d": "Impersonation / Phishing"}},
    "4": {"label": "Harassment / Hate",
          "children": {"4a": "Bullying or Harassment", "4b": "Unwanted Contact",
                       "4c": "Hate Speech or Symbols",
                       "4d": "Doxxing / Revealing Private Info"}},
    "5": {"label": "Nudity or Sexual Activity",
          "children": {"5a": "Threatening to Share / Sharing Nude Images",
                       "5b": "Explicit Content",
                       "5c": "Explicit Content Involving a Child"}},
    "6": {"label": "Violence / Threats",
          "children": {"6a": "Credible Threat to Safety",
                       "6b": "Glorifying Violence / Terrorism",
                       "6c": "Violent Death / Severe Injury / Animal Abuse",
                       "6d": "Threatening to Share / Sharing Nude Images"}},
    "7": {"label": "None or I don’t know", "children": {}}
}
#just a class to set up the states
class State(Enum):
    REPORT_START = auto()
    AWAITING_MESSAGE = auto()
    #new added state to choose what category for rep
    CHOOSE_CAT        = auto()      #select category for report
    #now have user select a more specific area of reporting issue
    CHOOSE_SUBCAT     = auto()
    CONFIRM_BLOCK     = auto()      #confirmation whether user wants to block other
    REPORT_DONE = auto()

class Report:
    START_KEYWORD = "report"
    CANCEL_KEYWORD = "cancel"
    HELP_KEYWORD = "help"

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.category_code = None     #track category user is reporting
        self.reporter      = None     #stores info about the person reporting the scene

    async def handle_message(self, message):
        '''
        This function makes up the meat of the user-side reporting flow. It defines how we transition between states and what
        prompts to offer at each of those states. You're welcome to change anything you want; this skeleton is just here to
        get you started and give you a model for working with Discord.
        '''

        if message.content == self.CANCEL_KEYWORD:
            self.state = State.REPORT_DONE
            return ["Report cancelled."]

        if self.state == State.REPORT_START:
            self.reporter = message.author
            reply =  "Thank you for starting the reporting process. "
            reply += "Say `help` at any time for more information.\n\n"
            reply += "Please copy paste the link to the message you want to report.\n"
            reply += "You can obtain this link by right-clicking the message and clicking `Copy Message Link`."
            self.state = State.AWAITING_MESSAGE
            return [reply]

        if self.state == State.AWAITING_MESSAGE:
            # Parse out the three ID strings from the message link
            m = re.search('/(\d+)/(\d+)/(\d+)', message.content)
            if not m:
                return ["I'm sorry, I couldn't read that link. Please try again or say `cancel` to cancel."]
            guild = self.client.get_guild(int(m.group(1)))
            if not guild:
                return ["I cannot accept reports of messages from guilds that I'm not in. Please have the guild owner add me to the guild and try again."]
            channel = guild.get_channel(int(m.group(2)))
            if not channel:
                return ["It seems this channel was deleted or never existed. Please try again or say `cancel` to cancel."]
            try:
                message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return ["It seems this message was deleted or never existed. Please try again or say `cancel` to cancel."]

            # Here we've found the message - it's up to you to decide what to do next!
            #get the message user wants to block
            self.message = message
            #transition state to ask what category user wishes to report message for
            self.state = State.CHOOSE_CAT
            #display the 7 abuse categories for user to select from (1-7)
            return [self.category_prompt(top_level=True)]
        #this is the choose category state
        if self.state == State.CHOOSE_CAT:
            #stip the message to remove any whitesepace
            code = message.content.strip()
            #edge case if user decides to place anything that is not 1-7
            if code not in OPTIONS_TREE:
                return ["Please reply with a number 1-7 from the list above or say `cancel`."]
            #save the top level code user chose
            self.category_code = code
            #if the selected top level has a subcategory transition to next state
            if OPTIONS_TREE[code]["children"]:
                self.state = State.CHOOSE_SUBCAT
                return [self.category_prompt(top_level=False)]
            #if not move to confirmation block
            self.state = State.CONFIRM_BLOCK
            return [self.thank_you_message(), self.block_prompt()]
        #this is the actions made when we are in the subcategory state
        if self.state == State.CHOOSE_SUBCAT:
            #get the potential subcategory code
            sub = message.content.strip().lower()
            #check the subcategory from the top level selected category
            children = OPTIONS_TREE[self.category_code]["children"]
            #if it is not a valid a category, have them reply again or cancel
            if sub not in children:
                return ["Please reply with one of the sub-codes shown above or say `cancel`."]
            #save selected subcategory user has chosen
            self.category_code = sub
            #now move to confirm block state
            self.state = State.CONFIRM_BLOCK
            return [self.thank_you_message(), self.block_prompt()]
        #this is the confirm block state
        if self.state == State.CONFIRM_BLOCK:
            #block user/message if user says yes
            if message.content.lower().startswith("y"):
                await self.client.block_user(self.reporter, self.message.author)  # ★ ADDED
                note = "User has been blocked (simulated)."
            #otherwise let them know they can block user later on setting or smth idk
            else:
                note = "No problem. you can block them later from settings."

            #create the enqueue system
            await self.client.enqueue_report({
                "reporter":      self.reporter, #captures who submitted the report
                "offender":      self.message.author, #captures the identity of the person who sent reported msg
                "jump_url":      self.message.jump_url, #has link to msg that was reported
                "category_code": self.category_code, #gives info about the category and subcategory chosen
                "label":         self.label_for_code(self.category_code) #generate a label for code
            })

            self.state = State.REPORT_DONE
            return [note,
                    "Report complete. Thank you for helping keep the community safe!"]

        # ---------------- fallback -----------------------------------------
        return []


    def report_complete(self):
        return self.state == State.REPORT_DONE
    #create a helper function for starting user reporting flow via query (J)
    def category_prompt(self, *, top_level):
        if top_level:
            header = "Please select a reason for reporting this [message/user]"
            body   = "\n".join(f"{k}. {v['label']}" for k, v in OPTIONS_TREE.items())
            return f"{header}\n{body}"
        parent = OPTIONS_TREE[self.category_code]
        header = ("If you are able, please attempt to categorize this message. "
                  "This helps our content moderation team to respond in an "
                  "efficient and correct manner.\n"
                  f"**More detail for {parent['label']}** (reply with code):")
        body   = "\n".join(f"{k}  {label}" for k, label in parent["children"].items())
        return f"{header}\n{body}"
    #this is a helper function for when the user is done with reporting issue
    #added the extra line for categories 4-7 as reporting flow shows
    def thank_you_message(self):#j
        critical = self.category_code.startswith(("4", "5", "6", "7"))  # ← includes 7
        extra = (", as well as notifying local authorities if necessary"
                 if critical else "")
        return ("Thank you for reporting. Our content moderation team will review "
                "the message and decide on the appropriate action. "
                f"This may include post and/or account removal{extra}.")
    @staticmethod#helper does not need a state so i have to use a static method
    #sends prompt that asks if user wishes to block author of harmful msg
    def block_prompt():
        return ("Would you like to block this user? You will no longer be "
                "able to receive any messages from them, and they will not "
                "be notified. (yes / no)")

    #turns the code into label
    def label_for_code(self, code: str) -> str:
        if len(code) == 1:
            return OPTIONS_TREE[code]["label"]
        parent = OPTIONS_TREE[code[0]]
        return parent["children"][code]



