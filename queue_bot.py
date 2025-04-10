import discord
from discord.ext import commands
import requests
import json
import os
import csv
import asyncio
from dotenv import load_dotenv
import re
import random

# Load environment variables from .env file
load_dotenv()


# Constants
DEFAULT_BASE_URL = "https://queue.illinois.edu"
API_PATH = "/q/api"
QUEUE_TOKEN = os.getenv("QUEUE_TOKEN", "")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GROUPS_CSV_PATH = os.getenv("GROUPS_CSV_PATH", "groups.csv")
CHECK_INTERVAL = int(
    os.getenv("CHECK_INTERVAL", "300")
)  # Default: check every 5 minutes

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Cache for group information
netid_to_group = {}
group_to_members = {}
# Tracking of previously detected groups in queue
previous_groups_in_queue = {}


async def get_questions_for_queue(base_url, queue_id, token=None):
    """Fetch all questions for a specific queue"""
    # Set up the authentication
    headers = {}
    if token:
        headers["Private-Token"] = token

    url = f"{base_url}{API_PATH}/queues/{queue_id}/questions"

    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(
                f"Error fetching questions for queue {queue_id}: {response.status_code}"
            )
            return []
    except Exception as e:
        print(f"Exception in get_questions_for_queue: {str(e)}")
        return []


def extract_netid(question):
    """Extract NetID from a question JSON object"""
    try:
        if (
            question
            and "askedBy" in question
            and question["askedBy"]
            and "netid" in question["askedBy"]
        ):
            return question["askedBy"]["netid"]
        return None
    except Exception as e:
        print(f"Error extracting NetID: {str(e)}")
        return None


def load_groups_from_csv(csv_file_path):
    """
    Load group information from a CSV file.
    Returns a dictionary mapping netids to their group information.
    """
    netid_to_group = {}
    group_to_members = {}

    try:
        with open(csv_file_path, "r") as csvfile:
            csv_reader = csv.reader(csvfile)

            # Assuming the first row is headers
            headers = next(csv_reader)

            # Process each row (group)
            for group_idx, row in enumerate(csv_reader, 1):
                group_id = f"Group {group_idx}"
                members = []

                # Extract non-empty netids from the row
                for cell in row:
                    if cell.strip():  # Check if the cell is not empty
                        netid = cell.strip().lower()  # Normalize to lowercase
                        members.append(netid)
                        netid_to_group[netid] = group_id

                if members:  # Only add the group if it has members
                    group_to_members[group_id] = members

    except Exception as e:
        print(f"Error loading groups from CSV: {str(e)}")

    return netid_to_group, group_to_members


def check_group_members_in_queue(netids, netid_to_group, group_to_members):
    """
    Check if multiple members of the same group are in the queue.
    Returns a dictionary of groups with multiple members in the queue.
    """
    group_count = {}
    groups_with_multiple_members = {}

    for netid in netids:
        if netid in netid_to_group:
            group_id = netid_to_group[netid]
            group_count[group_id] = group_count.get(group_id, 0) + 1

            # If this is the first member of this group we've seen, initialize the list
            if group_id not in groups_with_multiple_members:
                groups_with_multiple_members[group_id] = []

            # Add this member to the list
            groups_with_multiple_members[group_id].append(netid)

    # Filter out groups with only one member in the queue
    return {
        group_id: members
        for group_id, members in groups_with_multiple_members.items()
        if len(members) > 1
    }


def format_groups_message(groups_in_queue, group_to_members):
    """Format a message with information about groups with multiple members in the queue"""
    if not groups_in_queue:
        return "No groups with multiple members found in the queue."

    message = "⚠️ **GROUPS WITH MULTIPLE MEMBERS IN QUEUE** ⚠️\n\n"

    for group_id, members in groups_in_queue.items():
        message += f"**{group_id}**:\n"
        message += f"• Members in queue: {', '.join(members)}\n"

        # Show all members of the group for context
        all_members = group_to_members.get(group_id, [])
        not_in_queue = [m for m in all_members if m not in members]
        if not_in_queue:
            message += f"• Members not in queue: {', '.join(not_in_queue)}\n"

        message += "\n"

    return message


@bot.event
async def on_ready():
    """Event handler for when the bot has connected to Discord"""
    print(f"{bot.user.name} has connected to Discord!")

    # Load group information
    global netid_to_group, group_to_members
    netid_to_group, group_to_members = load_groups_from_csv(GROUPS_CSV_PATH)
    print(f"Loaded {len(group_to_members)} groups from {GROUPS_CSV_PATH}")

    # Start the background task for queue checking
    bot.loop.create_task(check_queue_periodically())


async def check_queue_periodically():
    """Background task to periodically check the queue for group members"""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            # Only proceed if we're connected and there are groups loaded
            if group_to_members:
                await check_queue_for_groups()

            # Wait for the next check
            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"Error in check_queue_periodically: {str(e)}")
            await asyncio.sleep(60)  # Wait a minute before retrying on error


async def check_queue_for_groups():
    """Check the queue for group members and send alerts if found"""
    global previous_groups_in_queue

    # Use the first command line argument as the queue ID if provided
    queue_id = os.getenv("DEFAULT_QUEUE_ID", "")
    if not queue_id:
        print(
            "No queue ID specified. Set the DEFAULT_QUEUE_ID environment variable."
        )
        return

    # Get questions for the specified queue
    questions = await get_questions_for_queue(
        DEFAULT_BASE_URL, queue_id, QUEUE_TOKEN
    )

    if not questions:
        print(
            f"No questions found for queue {queue_id} or error fetching questions."
        )
        return

    # Extract NetIDs from questions
    netids = []
    for question in questions:
        netid = extract_netid(question)
        if netid:
            netids.append(netid)

    # Check if multiple members of the same group are in the queue
    groups_in_queue = check_group_members_in_queue(
        netids, netid_to_group, group_to_members
    )

    # Determine which groups are new compared to the previous check
    new_groups = {}
    for group_id, members in groups_in_queue.items():
        if group_id not in previous_groups_in_queue:
            # This group wasn't in the queue before
            new_groups[group_id] = members
        else:
            # This group was in the queue before, but check if there are new members
            prev_members = set(previous_groups_in_queue[group_id])
            current_members = set(members)
            if not prev_members.issuperset(current_members):
                # There are new members in this group
                new_groups[group_id] = members

    # If there are new groups with multiple members in the queue, send an alert
    if new_groups:
        alert_channel_id = int(os.getenv("ALERT_CHANNEL_ID", "0"))
        if alert_channel_id > 0:
            channel = bot.get_channel(alert_channel_id)
            if channel:
                message = format_groups_message(new_groups, group_to_members)
                await channel.send(message)
            else:
                print(f"Could not find channel with ID {alert_channel_id}")

    # Update the previous groups in queue for the next check
    previous_groups_in_queue = groups_in_queue

def check_message_format(netids, topics):
    regex = "^\[(MP|Conceptual)\] ,Group \d+, Computer \d+ : .+$"
    message = "**QUESTION WITH INCORRECT FORMAT:**\n\n"
    for i in range(len(topics)):
        if not re.match(regex, topics[i]):
            message += f"Question {i} with netid {netids[i]} has wrong format: {topics[i]}"
    
    return message


@bot.command(name='checkqueue')
async def check_queue_command(ctx, queue_id=None):
    """
    Command to check for groups in the queue
    Usage: !checkqueue [queue_id]
    """
    if not queue_id:
        queue_id = os.getenv("DEFAULT_QUEUE_ID", "")
        if not queue_id:
            await ctx.send(
                "No queue ID specified. Please provide a queue ID or set the DEFAULT_QUEUE_ID environment variable."
            )
            return
    

    await ctx.send(f"Checking queue {queue_id} for group members...")

    # Get questions for the specified queue
    questions = await get_questions_for_queue(
        DEFAULT_BASE_URL, queue_id, QUEUE_TOKEN
    )

    if not questions:
        await ctx.send(
            f"No questions found for queue {queue_id} or error fetching questions."
        )
        return
    
    # Extract NetIDs and text from questions
    netids = []
    topics = []
    for question in questions:
        netid = extract_netid(question)
        if netid:
            netids.append(netid)
            if question["topic"]: # Put this under here to avoid length mismatch
                topics.append(question["topic"])
    
    await ctx.send(f"Found {len(netids)} questions with NetIDs in the queue.")

    # Check if multiple members of the same group are in the queue
    groups_in_queue = check_group_members_in_queue(
        netids, netid_to_group, group_to_members
    )

    # Format and send the message
    message = format_groups_message(groups_in_queue, group_to_members)

    await ctx.send(message)

    # Check for questions with wrong format

    await ctx.send("Checking for questions with wrong format...")
    message = check_message_format(netids, topics)
    await ctx.send(message)

@bot.command(name="checkstaff")
async def check_staff_command(ctx, queue_id=None):
    """
    Command to check for staff in the queue
    Usage: !checkstaff [queue_id]
    """
    if not queue_id:
        queue_id = os.getenv("DEFAULT_QUEUE_ID", "")
        if not queue_id:
            await ctx.send("No queue ID specified. Please provide a queue ID or set the DEFAULT_QUEUE_ID environment variable.")
            return
    
    queue_info = await get_queue_info(DEFAULT_BASE_URL, queue_id, QUEUE_TOKEN)
    if not queue_info:
        await ctx.send(f"Error fetching queue info for queue {queue_id}.")
        return
    
    staff_str = ""
    #Extract activeStaff from queue_info
    if queue_info["activeStaff"] == []:
        staff_str = f"No active staff found for queue {queue_id}."
    else :
        for staff in queue_info["activeStaff"]:
            staff_name = staff["user"]["name"]
            staff_str += f"{staff_name}, "
        staff_str = staff_str[:-2]  # Remove the trailing comma and space
        staff_str = f"Currently {staff_str} are on duty."

    await ctx.send(f"Queue {queue_id} found. {staff_str}")
    


@bot.command(name='reloadgroups')
async def reload_groups_command(ctx, csv_path=None):
    """
    Command to reload the groups CSV file
    Usage: !reloadgroups [csv_path]
    """
    global netid_to_group, group_to_members

    if not csv_path:
        csv_path = GROUPS_CSV_PATH

    await ctx.send(f"Reloading groups from {csv_path}...")

    try:
        netid_to_group, group_to_members = load_groups_from_csv(csv_path)
        await ctx.send(f"Successfully loaded {len(group_to_members)} groups!")
    except Exception as e:
        await ctx.send(f"Error loading groups: {str(e)}")


@bot.command(name="setinterval")
async def set_interval_command(ctx, seconds: int):
    """
    Command to set the check interval in seconds
    Usage: !setinterval 300
    """
    global CHECK_INTERVAL

    if seconds < 60:
        await ctx.send("Interval must be at least 60 seconds.")
        return

    CHECK_INTERVAL = seconds
    await ctx.send(f"Check interval set to {seconds} seconds.")

@bot.command(name='levquote')
async def lev_quote_command(ctx):
    """
    Command to send a quote from the big lev
    Usage: !levquote
    """
    quotes = [
        "You pay me with your tuition and I still drive a shitty car",
        "Some of you are playing games in my class, not very well I might add",
        "I don't know how anyone taught this before Severance",
        "Is it normal to be confused? For you, yes",
    ]
    
    await ctx.send("Here's a daily inspirational quote from the big lev:")
    await ctx.send(random.choice(quotes))


@bot.event
async def on_command_error(ctx, error):
    """Event handler for command errors"""
    if isinstance(error, commands.errors.CommandNotFound):
        pass  # Ignore command not found errors
    else:
        await ctx.send(f"An error occurred: {str(error)}")
        print(f"Error in command {ctx.command}: {str(error)}")


# Run the bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(
            "Error: No Discord token provided. Set the DISCORD_TOKEN environment variable."
        )
    elif not QUEUE_TOKEN:
        print(
            "Error: No Queue token provided. Set the QUEUE_TOKEN environment variable."
        )
    else:
        bot.run(DISCORD_TOKEN)
