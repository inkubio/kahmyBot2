# kahmyBot2
This telegram bot is utilised for the election season (kähmykausi) of Inkubio.

How to use:

1. Create a config.py file and fill it with the required information (example)
   1. bot_token = "your_token" 
      1. Get this from BotFather
   2. group_ids = ["id1", "id2"]
      1. You can get these for example by adding @getidsbot to the chats
      2. But also other ways to obtain this
   3. forum_url = "https://yourforum.com/"
   4. Optional: stats_file = "/path/to/kahmy_stats.json"
      1. Where the kähmy statistics are stored, defaults to kahmy_stats.json next to the script
   5. Optional: discourse_api_key = "key" and discourse_api_username = "system"
      1. Only needed if the forum topics are not publicly readable. Used to look up who started a topic the bot has not seen yet


2. Configure the Discourse forum webhook settings
   1. Your forum -> Additional settings -> Webhooks -> Create
   2. URL where your code is working (local server or hosted somewhere)
   3. Content type: apllication/json
   4. Select triggering events (creation of topics and posts for this, edits are ignored)
   5. Select in which discussion areas these events trigger can happen
   6. Tick "Active"
   7. Should be working now


3. Check the category_id's so you can determine which message to send (toimari/hallitus)
4. Run the kahmy_bot.py file.
5. Wait for someone to make a post to your forum and enjoy.
6. The messages contain a link to the topic in question for easy access from the chat

## Statistics

Every chain and comment is stored in the stats JSON file, so the bot remembers past kähmys over restarts.

- Grill temperature: a fire heating a small room. A comment adds 1 log (+40 °C to a cold room), a new chain adds 3 logs (+118 °C).
  The room cools down (about 60 % of the heat is left after 12 h), a comment every 4 h keeps it at 200 °C and it never goes over 3000 °C.
  Going over 150, 300 and 450 °C sends "Grilli lämpenee", "Ilmeisesti grilli{weekday}" and "Liekkeihn bensaa sataa".
- Every day at 20:00 (Europe/Helsinki), if anything happened since the previous recap, the bot sends a recap:
  chains and comments (comments split into questions and answers, a comment from the chain's author is an answer),
  top 3 commenters and chains, a temperature graph, the maximum temperature, and the yearly totals of
  hallituskähmys and toimarikähmys.

Bot is developed using Python version 3.12 and needed packages for run the project are in requirements.txt.

To help with the usage it is also recommended to tap from the forum API settings "retry web hook events". This ensures that if the messaging to the chat fails, the forum will try to push an update again (in 1, 5, 25 and 125 minutes) until successfull.
This way even if something breaks, every message should get through and as a last resort from the logs, you can manually push individual events again.
