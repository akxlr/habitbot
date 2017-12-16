import os
import time
import json
import time
import pytz
import random
import hashlib
import datetime
import traceback
from functools import wraps
from datetime import timedelta
from slackclient import SlackClient

SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
CHANNEL = 'C8DQG32CT' # general
DB_FILE = 'db.json'
COMMAND_PREFIX = '!habit'
CHECK_DELAY_SECONDS = 5 * 60 # 5 minutes
QUOTES_FILE = 'quotes.json'
USAGE_STR = """
Usage:
    Add a habit: *{0} add <name> <window_start> <window_end> <penalty> <description>*
    Remove a habit: *{0} rm <name>*
    List all habits: *{0} list*
    Post daily proof: *{0} done <name>*

Example:
    {0} add getup 08:00 09:00 $50 Get out of bed and take a photo of my breakfast

    I will expect you to post '{0} done getup' between 8:00 and 9:00 Sydney time every day, and if you don't I will publicly shame you into paying $50. I don't care about weekends or holidays."
""".format(COMMAND_PREFIX)

SHAME_MSG = "😠 *SHAME! {user_name}, did you do {name} today? 😠* I expected to see a post between {window_start} and {window_end} saying '{prefix} done {name}'. The prescribed penalty for {user_name} is: *{penalty}*"

slack_client = SlackClient(SLACK_BOT_TOKEN)

class BotError(Exception):
    def __init__(self, message):
        self.message = message

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.loads(f.read())
    else:
        return {}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        f.write(json.dumps(db))

def db_method(func):
    """
    Decorator for functions that access db.json - passes db as the first argument to the
    decorated function, and writes the returned db back to file. Decorated function should
    either return db or (db, actual_return_val).
    """
    @wraps(func)
    def wrapped(*args, **kwargs):
        db = load_db()
        res = func(db, *args, **kwargs)
        if isinstance(res, tuple):
            save_db(res[0])
            return res[1]
        else:
            save_db(res)

    return wrapped

@db_method
def add_habit(db, habit):
    if 'habits' not in db:
        db['habits'] = {}

    if habit['name'] in db['habits']:
        raise BotError('Habit {0} already exists! Remove it with !habit rm {0}.'.format(habit['name']))

    db['habits'][habit['name']] = {
        'name': habit['name'],
        'user_id': habit['user_id'],
        'user_name': habit['user_name'],
        'window_start': habit['window_start'],
        'window_end': habit['window_end'],
        'penalty': habit['penalty'],
        'description': habit['description'],
        'last_completed': -1,
        'shamed': False,
    }
    return db

def syd_to_server(t):
    h, m = t
    dt = datetime.datetime.now(pytz.timezone('Australia/Sydney')).replace(hour=h, minute=m).astimezone()
    return [dt.hour, dt.minute]

def in_window(ts, window_start, window_end):
    start = syd_to_server([int(x) for x in window_start.split(":")])
    end = syd_to_server([int(x) for x in window_end.split(":")])
    today_start = datetime.datetime.now().replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    today_end = datetime.datetime.now().replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    return today_start <= datetime.datetime.fromtimestamp(ts) <= today_end

@db_method
def check_habits(db):
    if 'habits' not in db:
        db['habits'] = {}
    ts = time.time()
    shamed = []
    for name, habit in db['habits'].items():

        end = syd_to_server([int(x) for x in habit['window_end'].split(":")])
        today_end = datetime.datetime.now().replace(hour=end[0], minute=end[1], second=0, microsecond=0)
        now = datetime.datetime.now()
        mins_since_window_end = (now - today_end).seconds

        if mins_since_window_end <= CHECK_DELAY_SECONDS:
            # This habit's window just ended, check that it was completed
            if not in_window(habit['last_completed'], habit['window_start'], habit['window_end']) and not habit['shamed']:
                shame_msg = SHAME_MSG.format(**habit, prefix=COMMAND_PREFIX)
                shamed.append(name)
                send_msg(shame_msg)

    for name in shamed:
        db['habits'][name]['shamed'] = True
    return db

@db_method
def habit_done(db, habit_name):
    db['habits'][habit_name]['last_completed'] = time.time()
    return db

@db_method
def rm_habit(db, habit_name):
    if 'habits' not in db:
        raise BotError("Habit {} does not exist!".format(habit_name))
    del db['habits'][habit_name]
    return db

@db_method
def list_habits(db):
    if 'habits' not in db:
        db['habits'] = {}
    send_msg(json.dumps(db['habits'], indent=4, sort_keys=True))
    return db

@db_method
def get_random_quote(db):
    if 'quotes_used' not in db:
        db['quotes_used'] = []
    with open(QUOTES_FILE, 'r') as f:
        quotes = json.loads(f.read())['quotes']
    unused_quotes = []
    for quote in quotes:
        quote_hash = str(hashlib.md5(str(quote).encode()).hexdigest())
        if quote_hash not in db['quotes_used']:
            unused_quotes.append((quote, quote_hash))
    quote, quote_hash = random.choice(unused_quotes)
    db['quotes_used'].append(quote_hash)
    return db, quote

def send_msg(msg):
    slack_client.api_call("chat.postMessage", channel=CHANNEL, text=msg, as_user=True)

def get_user_name(user_id):
    api_call = slack_client.api_call("users.list")
    if api_call.get('ok'):
        users = api_call.get('members')
        for user in users:
            if 'id' in user and user.get('id') == user_id:
                return user.get('name')
    raise Exception("Username for id {} not found".format(user_id))

def handle_command(command, user_id):

    fields = command.split()
    if fields[0] == 'add':
        # add <name> <window_start> <window_end> <penalty> <description>
        habit = {
            'name': fields[1],
            'user_id': user_id,
            'user_name': get_user_name(user_id),
            'window_start': fields[2],
            'window_end': fields[3],
            'penalty': fields[4],
            'description': ' '.join(fields[5:]),
        }
        add_habit(habit)
        send_msg("I've saved a new habit *{name}*! Remember to post proof each day between "
                 "{window_start} and {window_end} with *{prefix} done {name}*. Good luck 😍".format(**habit, prefix=COMMAND_PREFIX))
    elif fields[0] == 'rm':
        rm_habit(fields[1])
        send_msg("Habit *{0}* deleted.".format(fields[1]))
    elif fields[0] == 'list':
        list_habits()
    elif fields[0] == 'done':
        habit_done(fields[1])
        quote = get_random_quote()
        send_msg("💯 Hooray! *{0}* done for the day. _{1}_ - {2}".format(fields[1], quote['text'], quote['author']))
    else:
        raise BotError("I don't know what you mean by '{}'".format(command))

def handle_raw(output):
    for o in output:
        if o['type'] == 'message':
            msg = o['text']
            if msg == COMMAND_PREFIX:
                send_msg(USAGE_STR)
            elif msg.startswith(COMMAND_PREFIX):
                handle_command(msg[len(COMMAND_PREFIX):], o['user'])

if __name__ == "__main__":
    READ_WEBSOCKET_DELAY = 1
    if slack_client.rtm_connect():
        print("HabitBot connected and running!")
        while True:
            # Handle messages
            try:
                handle_raw(slack_client.rtm_read())
            except BotError as e:
                send_msg("I can't do that. {}".format(e.message))
            except Exception as e:
                send_msg('Fuck! Something went horribly wrong: "{}"'.format(traceback.format_exc()))
                send_msg(USAGE_STR)

            # Check for missed habits
            check_habits()

            time.sleep(READ_WEBSOCKET_DELAY)
    else:
        print("Connection failed. Invalid Slack token or bot ID?")
