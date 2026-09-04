from flask import Flask
from threading import Thread
import discord
from discord.ext import commands
import csv
import asyncio
import os
import json
from datetime import datetime
from collections import defaultdict

# Keep alive server
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

class RoleManager:
    def __init__(self, guild):
        self.guild = guild
        self.checkpoint_file = f'checkpoint_{guild.id}.json'
        self.batch_size = 1000
        self.chunk_size = 10000

    async def load_checkpoint(self):
        try:
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'processed_users': [], 'total_success': 0, 'total_errors': 0}

    async def save_checkpoint(self, processed_users, total_success, total_errors):
        checkpoint = {
            'processed_users': processed_users,
            'total_success': total_success,
            'total_errors': total_errors,
            'timestamp': datetime.now().isoformat()
        }
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f)

    async def process_chunk(self, users_chunk, role, action, status_message, start_idx, total_users):
        success_count = 0
        error_count = 0
        error_log = []
        start_time = datetime.now()

        for i in range(0, len(users_chunk), self.batch_size):
            batch = users_chunk[i:i + self.batch_size]
            try:
                tasks = []
                members_to_update = []

                # First try to get members from cache
                for user_id in batch:
                    try:
                        member = self.guild.get_member(int(user_id))
                        if not member:
                            member = await self.guild.fetch_member(int(user_id))

                        if member:
                            if action == 'add' and role not in member.roles:
                                members_to_update.append(member)
                            elif action == 'remove' and role in member.roles:
                                members_to_update.append(member)
                        else:
                            error_log.append(f"Member not found: {user_id}")
                            error_count += 1
                    except discord.NotFound:
                        error_log.append(f"Member not found: {user_id}")
                        error_count += 1
                    except Exception as e:
                        error_log.append(f"Error with user {user_id}: {str(e)}")
                        error_count += 1

                # Bulk role updates
                if members_to_update:
                    if action == 'add':
                        tasks = [member.add_roles(role) for member in members_to_update]
                    else:
                        tasks = [member.remove_roles(role) for member in members_to_update]

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    success_count += sum(1 for r in results if not isinstance(r, Exception))
                    error_count += sum(1 for r in results if isinstance(r, Exception))

                # Update status every 100 users
                if i % 100 == 0:
                    total_processed = start_idx + i + len(batch)
                    elapsed_time = (datetime.now() - start_time).total_seconds()
                    users_per_second = total_processed / elapsed_time if elapsed_time > 0 else 0
                    remaining_users = total_users - total_processed
                    est_time_remaining = remaining_users / users_per_second if users_per_second > 0 else 0

                    await status_message.edit(content=
                        f"```\n"
                        f"Progress: {total_processed:,} / {total_users:,} users\n"
                        f"Successful operations: {success_count:,}\n"
                        f"Errors: {error_count:,}\n"
                        f"Estimated time remaining: {est_time_remaining/60:.1f} minutes\n"
                        f"```"
                    )

            except Exception as e:
                error_log.append(f"Batch error: {str(e)}")
                error_count += len(batch)

        return success_count, error_count, error_log

@bot.command()
@commands.has_permissions(manage_roles=True)
async def massroles(ctx, action: str, role_name: str, start_index: int = 0):
    """
    Process roles for large number of users
    Usage: !massroles <add/remove> <role_name> [start_index]
    """
    if action.lower() not in ['add', 'remove']:
        await ctx.send("Invalid action! Use 'add' or 'remove'")
        return

    if not ctx.message.attachments:
        await ctx.send("Please attach a CSV file!")
        return

    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"Role '{role_name}' not found!")
        return

    if role.position >= ctx.guild.me.top_role.position:
        await ctx.send(
            f"I can't manage the role '{role_name}' because it's higher than "
            f"or equal to my own top role. Move my role above it and try again."
        )
        return

    manager = RoleManager(ctx.guild)
    checkpoint = await manager.load_checkpoint()

    chunk_start = start_index

    try:
        attachment = ctx.message.attachments[0]
        csv_content = await attachment.read()
        csv_str = csv_content.decode('utf-8').splitlines()
        csv_reader = csv.reader(csv_str)
        next(csv_reader, None)  # Skip header

        user_ids = [row[0].strip() for row in csv_reader if len(row) > 0 and row[0].strip().isdigit()]

        processed_set = set(checkpoint['processed_users'])
        if processed_set:
            skipped = len(user_ids) - len([u for u in user_ids if u not in processed_set])
            user_ids = [u for u in user_ids if u not in processed_set]
            await ctx.send(f"Resuming: skipping {skipped:,} already-processed users from checkpoint.")

        total_users = len(user_ids)

        if total_users == 0:
            await ctx.send("No users left to process!")
            return

        if start_index >= total_users:
            await ctx.send("Start index is larger than remaining number of users!")
            return

        status_message = await ctx.send("```\nInitializing role operation...```")
        while chunk_start < total_users:
            chunk_end = min(chunk_start + manager.chunk_size, total_users)
            current_chunk = user_ids[chunk_start:chunk_end]

            success_count, error_count, error_log = await manager.process_chunk(
                current_chunk, role, action, status_message, chunk_start, total_users
            )

            checkpoint['processed_users'].extend(current_chunk)
            checkpoint['total_success'] += success_count
            checkpoint['total_errors'] += error_count
            await manager.save_checkpoint(
                checkpoint['processed_users'],
                checkpoint['total_success'],
                checkpoint['total_errors']
            )

            if error_log:
                error_text = "\n".join(error_log)
                for i in range(0, len(error_text), 1900):
                    await ctx.send(f"```\n{error_text[i:i+1900]}```")

            chunk_start = chunk_end

        await status_message.edit(content=
            f"```\n"
            f"Operation completed!\n"
            f"Successful operations: {checkpoint['total_success']:,}\n"
            f"Errors: {checkpoint['total_errors']:,}\n"
            f"```"
        )

    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")
        await ctx.send(f"You can resume from index {chunk_start} using:\n"
                      f"!massroles {action} {role_name} {chunk_start}")

@massroles.error
async def massroles_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the 'Manage Roles' permission to use this command.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

@bot.command()
@commands.has_permissions(administrator=True)
async def shutdown(ctx):
    """Safely shuts down the bot"""
    msg = await ctx.send("```\nShutting down bot...\nYou can restart the bot using the Run button```")
    await asyncio.sleep(1)
    await msg.edit(content="```\nBot is shutting down...\nGoodbye! 👋\nUse Run to restart```")
    await asyncio.sleep(2)
    await bot.close()

@shutdown.error
async def shutdown_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Administrator permission to use this command.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

# Start the bot
token = os.getenv('DISCORD_TOKEN')
if not token:
    print("ERROR: Bot token not found!")
    exit()

keep_alive()
bot.run(token)
