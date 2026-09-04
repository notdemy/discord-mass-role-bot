# discord-mass-role-bot

> [!NOTE]
> Built for a one-time job, to meet that specific requirement, not as a polished, reusable tool. It can be optimized a lot further (see **Known limitations** below), but it does the job if you want to use it as-is.

## Problem Statement

In January 2025, DIN ran a testnet campaign. Complete a few onchain tasks, claim some test tokens, get a Discord role once you're done. It went well. Over 293K people took part, and more than a million test tokens were claimed. DIN posted the wrap-up on X:

<img src="assets/campaign-tweet.png" alt="DIN's campaign wrap-up tweet: 293.15K participants, 1,156,960 test tokens claimed, and the line 'Now that the campaign is over, we'll start distributing Discord roles.'" width="480">

[x.com/din_lol_/status/1887049389388259620](https://x.com/din_lol_/status/1887049389388259620?s=20)

Except the role never actually got given out. Whoever set up the reward on the platform's side forgot to wire the Discord role into it. People finished every task, got nothing, and came straight to the server asking what happened. Fair enough.

## Solution

I was on the team handling it. There was no bulk button to give a role to tens of thousands of people, not in Discord, not in the platform we used. What we did have was a spreadsheet of every completed user's Discord ID. So I built a bot to take that CSV and work through it: resolve each user, apply the role, and pick back up if it gets interrupted partway through.

Here are two real test runs from Feb 7, while I was still fixing the CSV format. Excel kept turning long Discord IDs into scientific notation, and the bot needed to catch that before running on the real list.

<img src="assets/test-run-success.png" alt="Discord message from 07-02-2025 showing !bulkroles add with a CSV of user IDs and the bot replying Operation completed, Successful operations: 5, Errors: 0" width="850">

<img src="assets/test-run-error-log.png" alt="Discord message from 07-02-2025 showing the bot catching malformed scientific-notation user IDs in a test CSV and logging Invalid user ID for each one" width="850">

## What it does

One command, `!bulkroles`. Attach a CSV with each user's ID and the role they should get, tell it whether to add or remove, and it works through the whole list — in chunks, with live progress, and with enough state saved on disk to pick back up if it gets interrupted.

```
!bulkroles <add|remove> [start_index]
```

- Attach a CSV with two columns: `user_id,role_name`. Each row can name a different role.
- Entries are processed in chunks of 10,000, each chunk split into batches of 1,000.
- Progress, success/error counts, and an ETA are posted back to the channel and updated live.
- Every completed chunk is checkpointed to `checkpoint_<guild_id>.json`, keyed by `user_id:role_name`. If the bot restarts mid-run, the next `!bulkroles` call automatically skips everything already done.
- `!shutdown` cleanly closes the bot (admin-only).

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

If I were building it again:

- **No self-imposed rate limit.** Each batch fires up to 1,000 role-update requests concurrently via `asyncio.gather`; pacing is left entirely to discord.py's reactive rate-limit handling rather than a controlled cap (e.g. a `Semaphore`).
- **Checkpoints only every 10,000 entries**, not every batch — an interruption mid-chunk can cost up to 1,000 entries of re-work.
- **No dry-run / preview mode** — the first CSV you attach is the one that runs.

## Stack

Python · discord.py · Flask (keep-alive)
