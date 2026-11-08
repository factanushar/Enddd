SF ARMY WINNER NOTICE BOT - QUICK SETUP

1) Open config.py and paste your NEW bot token in BOT_TOKEN.
2) Keep ADMIN_IDS as your Telegram admin IDs (update if needed).
3) Check the required channel/group IDs and invite links in CHANNELS.
4) Add this new bot as an administrator in every required channel/group with permission to view members (and add it as admin in the giveaway channel only if you want it to post there manually later).
5) Install dependencies: pip install -r requirements.txt
6) Start: python main.py
7) In the new bot, send /admin -> Data Recovery -> upload your original giveaway database file (the .db backup from the old bot).
8) Users send /start, join all required channels/groups, tap I Verified, then tap Giveaway & Show Winners Name.

ADMIN PANEL
/admin -> Broadcast: send one HTML-formatted text; it broadcasts to verified user IDs stored in the restored giveaway database.
/admin -> Backup Database: downloads the current giveaway .db file.
/admin -> Data Recovery: upload a valid original giveaway .db file. The bot keeps a safety copy before replacement.

IMPORTANT
- This is a separate results/announcement bot. It does not include referrals, coins, redeem, or giveaway-entry functions.
- The original database is read for winner results and user IDs; the new bot stores its own /start subscriber list separately.
- Telegram bots cannot message users who have never started the new bot or who blocked it. Broadcast reports failed deliveries.
- The restored database must contain users and giveaway_results tables. Use the final backup from the old giveaway bot.
- Do not upload a bot token or share it in public.
