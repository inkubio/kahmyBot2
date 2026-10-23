"""
KähmyBot2.0

A telegram bot to be used during Inkubio ry's election season.
The bot sends a notification to specified chats about each application and comments on them.

The bot functions around the forum page's (discourse) webhook, which pushes notifications of certain actions,
which can be specified in the forum settings.
In this case when a new topic or post is created in the forum under certain discussion areas
(again specified in the forum settings)

Questions etc. can be sent to the coder in question:
Aaro Kuusinen
kuusinen.aaro@gmail.com
TG: @apeoskari
"""

from flask import Flask, request, jsonify
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import stats
from stats import KAHMY_ID


app = Flask(__name__)
scheduler = None


def send_message_to_telegram(text, chat_ids):
    """
    Actually sending the message determined in the 'text' parameter to chats specified in 'chat_ids'
    'chat_ids' is a list of the chat ids of chats (groups) you want to send the message to.
    'text' contains the entire message to be sent, including HTML formatting.
    """
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print(f"Message sent to group {chat_id}")
        else:
            print(
                f"Failed to send message to group {chat_id}. Status code: {response.status_code}"
            )


def send_photo_to_telegram(photo, chat_ids):
    """Send the PNG image given as bytes in 'photo' to chats specified in 'chat_ids'"""
    url = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
    for chat_id in chat_ids:
        response = requests.post(
            url,
            data={"chat_id": chat_id},
            files={"photo": ("lampotila.png", photo, "image/png")},
        )
        if response.status_code == 200:
            print(f"Photo sent to group {chat_id}")
        else:
            print(
                f"Failed to send photo to group {chat_id}. Status code: {response.status_code}"
            )


def send_daily_recap():
    """Send the recap of the kähmys since the previous recap, if there were any"""
    recap = stats.build_recap()
    if recap is None:
        return
    text, photo = recap
    send_photo_to_telegram(photo, config.group_ids)
    send_message_to_telegram(text, config.group_ids)


def start_scheduler():
    global scheduler
    if scheduler is not None:
        return
    scheduler = BackgroundScheduler(timezone=stats.TZ)
    scheduler.add_job(
        send_daily_recap, CronTrigger(hour=20, minute=0, timezone=stats.TZ)
    )
    scheduler.start()


@app.route("/kahmybot_webhook", methods=["POST"])
def discourse_webhook():
    """Flask route to handle Discourse webhook POST requests"""
    data = request.json
    payload = {}

    # Only new posts count, edits etc. would otherwise be sent again
    event = request.headers.get("X-Discourse-Event")
    if event is not None and event != "post_created":
        return jsonify({"status": "ok"}), 200

    # Payload

    if "post" in data and data["post"]["category_id"] in (
        KAHMY_ID.HALLITUS,
        KAHMY_ID.TOIMARI,
    ):
        payload = data["post"]
    else:
        return jsonify({"status": "ok"}), 200

    # Gather all necessary data
    topic_slug = payload["topic_slug"]
    topic_title = payload["topic_title"]
    user_fullname = (
        payload["name"] if payload["name"] is not None else payload["username"]
    )
    topic_id = payload["topic_id"]
    post_number = payload["post_number"]
    category_id = payload["category_id"]

    url = f"{config.forum_url}/t/{topic_slug}/{topic_id}/{post_number}"

    message = ""
    # when post_number == 1 the post is the original post
    match (post_number, category_id):
        case (1, KAHMY_ID.HALLITUS):
            embedded_url = f"<a href='{url}'>hallituskähmy</a>"
            message = f"Uusi {embedded_url} henkilöltä\n<b>{user_fullname}</b>:\n{topic_title}"
        case (1, KAHMY_ID.TOIMARI):
            embedded_url = f"<a href='{url}'>toimarikähmy</a>"
            message = f"Uusi {embedded_url} henkilöltä\n<b>{user_fullname}</b>:\n{topic_title}"
        case (post_number, _) if post_number > 1:
            embedded_url = f"<a href='{url}'>kommentti</a>"
            message = f"Uusi {embedded_url} kähmyyn:\n{topic_title}\nhenkilöltä <b>{user_fullname}</b>"

    try:
        grill_message = stats.record_post(payload)
    except Exception as e:
        # Statistics must never prevent the notification itself
        print(f"Failed to record statistics: {e}")
        grill_message = None

    send_message_to_telegram(message, config.group_ids)
    if grill_message is not None:
        send_message_to_telegram(grill_message, config.group_ids)

    return jsonify({"status": "ok"}), 200


def create_app():
    start_scheduler()
    return app


if __name__ == "__main__":
    start_scheduler()
    # The reloader would start a second process and send the recap twice
    app.run(port=5000, debug=True, use_reloader=False)
