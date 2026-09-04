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
        self._role_cache = {}

    async def load_checkpoint(self):
        try:
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'processed_entries': [], 'total_success': 0, 'total_errors': 0}

    async def save_checkpoint(self, processed_entries, total_success, total_errors):
        checkpoint = {
            'processed_entries': processed_entries,
            'total_success': total_success,
            'total_errors': total_errors,
            'timestamp': datetime.now().isoformat()
        }
        with open(self.checkpoint_file, 'w') as f:
            json.dump(checkpoint, f)

    def resolve_role(self, role_name):
        if role_name not in self._role_cache:
            self._role_cache[role_name] = discord.utils.get(self.guild.roles, name=role_name)
        return self._role_cache[role_name]

    async def process_chunk(self, entries_chunk, action, status_message, start_idx, total_entries):
        success_count = 0
        error_count = 0
        error_log = []
        start_time = datetime.now()

        for i in range(0, len(entries_chunk), self.batch_size):
            batch = entries_chunk[i:i + self.batch_size]
            try:
                tasks = []
                updates = []  # (member, role) pairs

                for user_id, role_name in batch:
                    try:
                        role = self.resolve_role(role_name)
                        if not role:
                            error_log.append(f"Role not found: '{role_name}' (user {user_id})")
                            error_count += 1
                            continue

                        if role.position >= self.guild.me.top_role.position:
                            error_log.append(f"Can't manage role '{role_name}' — it's above my own top role (user {user_id})")
                            error_count += 1
                            continue

                        member = self.guild.get_member(int(user_id))
                        if not member:
                            member = await self.guild.fetch_member(int(user_id))

                        if member:
                            if action == 'add' and role not in member.roles:
                                updates.append((member, role))
                            elif action == 'remove' and role in member.roles:
                                updates.append((member, role))
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
                if updates:
                    if action == 'add':
                        tasks = [member.add_roles(role) for member, role in updates]
                    else:
                        tasks = [member.remove_roles(role) for member, role in updates]

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    success_count += sum(1 for r in results if not isinstance(r, Exception))
                    error_count += sum(1 for r in results if isinstance(r, Exception))

                # Update status every 100 entries
                if i % 100 == 0:
                    total_processed = start_idx + i + len(batch)
                    elapsed_time = (datetime.now() - start_time).total_seconds()
                    entries_per_second = total_processed / elapsed_time if elapsed_time > 0 else 0
                    remaining_entries = total_entries - total_processed
                    est_time_remaining = remaining_entries / entries_per_second if entries_per_second > 0 else 0

                    await status_message.edit(content=
                        f"```\n"
                        f"Progress: {total_processed:,} / {total_entries:,} users\n"
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
async def bulkroles(ctx, action: str, start_index: int = 0):
    """
    Process roles for large number of users
    Usage: !bulkroles <add/remove> [start_index]
    Attach a CSV with columns: user_id,role_name
    """
    if action.lower() not in ['add', 'remove']:
        await ctx.send("Invalid action! Use 'add' or 'remove'")
        return

    if not ctx.message.attachments:
        await ctx.send("Please attach a CSV file with columns: user_id,role_name")
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

        entries = [
            (row[0].strip(), row[1].strip())
            for row in csv_reader
            if len(row) > 1 and row[0].strip().isdigit() and row[1].strip()
        ]

        processed_set = set(checkpoint['processed_entries'])
        if processed_set:
            entry_keys = [f"{uid}:{role}" for uid, role in entries]
            skipped = sum(1 for key in entry_keys if key in processed_set)
            entries = [e for e, key in zip(entries, entry_keys) if key not in processed_set]
            if skipped:
                await ctx.send(f"Resuming: skipping {skipped:,} already-processed entries from checkpoint.")

        total_entries = len(entries)

        if total_entries == 0:
            await ctx.send("No entries left to process!")
            return

        if start_index >= total_entries:
            await ctx.send("Start index is larger than remaining number of entries!")
            return

        status_message = await ctx.send("```\nInitializing role operation...```")
        while chunk_start < total_entries:
            chunk_end = min(chunk_start + manager.chunk_size, total_entries)
            current_chunk = entries[chunk_start:chunk_end]

            success_count, error_count, error_log = await manager.process_chunk(
                current_chunk, action, status_message, chunk_start, total_entries
            )

            checkpoint['processed_entries'].extend(f"{uid}:{role}" for uid, role in current_chunk)
            checkpoint['total_success'] += success_count
            checkpoint['total_errors'] += error_count
            await manager.save_checkpoint(
                checkpoint['processed_entries'],
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
                      f"!bulkroles {action} {chunk_start}")

@bulkroles.error
async def bulkroles_error(ctx, error):
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
