# discord-mass-role-bot

## Problem Statement

In January 2025, DIN ran a testnet campaign — complete a few onchain tasks, claim some test tokens, get a Discord role once you're done. It went well: 293K+ people took part, over a million test tokens claimed. DIN posted the wrap-up on X:

<img src="assets/campaign-tweet.png" alt="DIN's campaign wrap-up tweet: 293.15K participants, 1,156,960 test tokens claimed, and the line 'Now that the campaign is over, we'll start distributing Discord roles.'" width="480">

Except the role part never actually happened. Whoever set up the reward on the platform's side forgot to wire the Discord role into it, so people finished every task, got nothing, and came straight to the server asking what was going on. Fair enough — I'd be annoyed too.

## Solution

I was on the team dealing with it, and there was no "give this role to tens of thousands of people" button anywhere — not in Discord, not in the platform we were using. What we did have was a spreadsheet of every completed user's Discord ID. So I put together a bot that could take that CSV and just work through it: resolve each user, apply the role, and not lose its place if it got interrupted 20,000 users in.

Below are two real test runs from Feb 7, while I was still working out the CSV format — Excel loves turning long Discord IDs into scientific notation, and the bot needed to catch that before it went anywhere near the real list.

<img src="assets/test-run-success.png" alt="Discord message from 07-02-2025 showing !bulkroles add with a CSV of user IDs and the bot replying Operation completed, Successful operations: 5, Errors: 0" width="850">

<img src="assets/test-run-error-log.png" alt="Discord message from 07-02-2025 showing the bot catching malformed scientific-notation user IDs in a test CSV and logging Invalid user ID for each one" width="850">

## What it does

One command, `!massroles`. Attach a CSV of Discord user IDs, tell it a role and whether to add or remove it, and it takes care of the rest — in chunks, with live progress, and with enough state saved on disk to pick back up if it gets interrupted.

```
!massroles <add|remove> <role_name> [start_index]
```

- Attach a CSV (`user_id` in the first column) to the command message.
- Users are processed in chunks of 10,000, each chunk split into batches of 1,000.
- Progress, success/error counts, and an ETA are posted back to the channel and updated live.
- Every completed chunk is checkpointed to `checkpoint_<guild_id>.json`. If the bot restarts mid-run, the next `!massroles` call automatically skips everyone already processed.
- `!shutdown` cleanly closes the bot (admin-only).

## Flow

```
Discord message → !massroles action role [start_index]
  → permission check (Manage Roles) → reply & stop if missing
  → validate: CSV attached, role exists, role below bot's own top role → reply & stop if not
  → parse CSV, load checkpoint, drop already-processed IDs
  → loop: next chunk (10k) → next batch (1k) → resolve members → apply role → update status
      (checkpoint saved after each chunk)
  → final summary posted
```

`!shutdown` is a separate, short path: admin check → farewell message → `bot.close()`.

A Flask thread (`keep_alive()`) runs the whole time independently of either command, for hosts that expect an HTTP port to stay open.

## Proof of Work

Live progress from the actual production runs, ~4K and ~40K users:

<img src="assets/progress-100.png" alt="Progress: 100 / 3,968 users, estimated time remaining 92.8 minutes" width="850">

<img src="assets/progress-1000.png" alt="Progress: 1,000 / 39,948 users, 16 errors, estimated time remaining 678.1 minutes" width="850">

<img src="assets/progress-27000.png" alt="Progress: 27,000 / 38,961 users, 112 errors, estimated time remaining 54.2 minutes" width="850">

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN
python complete-discord-bot.py
```

Requires the **Server Members** and **Message Content** privileged intents enabled in the Discord Developer Portal, and the bot's own role positioned above any role it needs to manage.

## Known limitations

This was written to solve an active incident, not to be a polished tool. If I were building it again:

- **No self-imposed rate limit.** Each batch fires up to 1,000 role-update requests concurrently via `asyncio.gather`; pacing is left entirely to discord.py's reactive rate-limit handling rather than a controlled cap (e.g. a `Semaphore`).
- **Checkpoints only every 10,000 users**, not every batch — an interruption mid-chunk can cost up to 1,000 users of re-work.
- **No dry-run / preview mode** — the first CSV you attach is the one that runs.

## Stack

Python · discord.py · Flask (keep-alive)
