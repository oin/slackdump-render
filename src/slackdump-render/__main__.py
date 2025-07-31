import argparse
import sqlite3
import datetime
import os
import json
import re
import emoji
from jinja2 import Environment, FileSystemLoader, select_autoescape
from tqdm import tqdm
from dataclasses import dataclass
from typing import Optional
from unicodedata import normalize
from urllib.parse import quote

@dataclass
class User:
	id: str
	name: str
	realname: str | None
	avatar_path: str | None

@dataclass
class Channel:
	id: str
	name: str
	slug: str
	is_private: bool
	members: list["User"]
	messages: list["Message"]

@dataclass
class File:
	id: str
	name: str
	path: str
	thumbnail_path: str | None

@dataclass
class Message:
	id: str
	date: datetime.datetime
	text: str | None
	user: User
	children: list["Message"]
	files: list["File"]
	parent: Optional["Message"] = None

def load_from_db(db_path, outputdir, only_public: bool = False, allowed_channels: list[str] | None = None):
	inputdir = os.path.dirname(db_path)

	conn = sqlite3.connect(db_path)

	# Fetch users
	cursor = conn.cursor()
	query = """
		SELECT id, username, data
		FROM S_USER
		ORDER BY LOAD_DTTM ASC
	"""
	cursor.execute(query)
	users = {}
	for id, username, data in tqdm(cursor.fetchall(), desc="Users"):
		if username is None:
			continue
		data = json.loads(data) if data else {}
		realname = data.get("real_name")

		avatar_path = retrieve_avatar(inputdir, outputdir, id)
		users[id] = User(id=id, name=username, realname=realname, avatar_path=avatar_path)
	cursor.close()

	# Fetch channels
	cursor = conn.cursor()
	query = """
		SELECT id, name, data
		FROM CHANNEL
		ORDER BY LOAD_DTTM ASC
	"""
	cursor.execute(query)

	channels = {}
	for id, name, data in tqdm(cursor.fetchall(), desc="Channels"):
		if allowed_channels:
			if id not in allowed_channels and name not in allowed_channels:
				continue
		slug = None
		is_private = (name and name.startswith('mpdm-')) or id.startswith('D')
		if is_private:
			if only_public:
				continue
			# Clean up the name for private multi-user channels
			# Example: mpdm-usera--userb--userc--userd-1 => usera, userb, userc, userd
			if name and name.startswith('mpdm-'):
				name = name.rsplit('-', 1)[0]
				name = name[5:].split('--')
				slug = '+'.join(name)
				name = ', '.join(name)
			elif id.startswith('D') and not name and data:
				data = json.loads(data)
				if "user" in data:
					userid = data["user"]
					name = users[userid].name if userid in users else None

		if not name or len(name) == 0:
			name = f"{id}"
		if not slug:
			slug = name
		if not slug:
			slug = "private-id"
		if is_private:
			slug = "@" + slug
		else:
			name = "#" + name
		channels[id] = Channel(id=id, name=name, slug=slug, is_private=is_private, members=[], messages=[])
	cursor.close()
	channel_ids = set(channels.keys())

	# Populate channels with users
	cursor = conn.cursor()
	query = f"""
		SELECT CHANNEL_ID, USER_ID
		FROM CHANNEL_USER
		WHERE CHANNEL_ID IN ({','.join(['?'] * len(channel_ids))})
	"""
	cursor.execute(query, tuple(channel_ids))
	for channel_id, user_id in tqdm(cursor.fetchall(), desc="Channel members"):
		if channel_id not in channels or user_id not in users:
			continue
		channels[channel_id].members.append(users[user_id])
	cursor.close()

	# Fetch messages
	cursor = conn.cursor()
	query = f"""
		SELECT id, ts, channel_id, parent_id, data
		FROM MESSAGE
		WHERE channel_id IN ({','.join(['?'] * len(channel_ids))})
		ORDER BY ts ASC
	"""
	cursor.execute(query, tuple(channel_ids))
	for id, ts, channel_id, parent_id, data in tqdm(cursor.fetchall(), desc="Messages"):
		data = json.loads(data)
		ts = datetime.datetime.fromtimestamp(float(ts))
		if "user" not in data:
			continue
		user = users[data["user"]]
		text = data.get("text")
		files = retrieve_uploaded_files(inputdir, outputdir, data)
		message = Message(id=id, date=ts, text=text, user=user, children=[], files=files)
		channels[channel_id].messages.append(message)
		if parent_id:
			parent = next((m for m in channels[channel_id].messages if m.id == parent_id), None)
			if parent:
				message.parent = parent
				parent.children.append(message)
	cursor.close()

	conn.close()

	return channels, users

def retrieve_avatar(inputdir, outputdir, id):
	avatar_path = None
	avatardir = os.path.join(inputdir, "__avatars", id)
	if os.path.exists(avatardir):
		# Use the first file found in the avatar directory
		avatar_files = os.listdir(avatardir)
		if avatar_files:
			avatar_path = os.path.relpath(os.path.join(avatardir, avatar_files[0]), outputdir)
	return avatar_path

def retrieve_uploaded_files(inputdir, outputdir, data):
	files = []
	if "files" not in data:
		return files

	for file in data["files"]:
		file_id = file["id"]
		filedir = os.path.join(inputdir, "__uploads", file_id)
		if os.path.exists(filedir):
			# Use the first file found in the uploads directory
			contents = os.listdir(filedir)
			if contents:
				path = os.path.relpath(os.path.join(filedir, contents[0]), outputdir)
				thumbnail_path = None
				if file["mimetype"].startswith("image/"):
					thumbnail_path = path
				f = File(id=file_id, name=file["name"], path=path, thumbnail_path=thumbnail_path)
				files.append(f)
	return files

def slack2html(text):
	text = re.sub(r"<(http[s]?://[^|>]+)\|([^>]+)>", r'<a href="\1">\2</a>', text)
	text = re.sub(r"<(http[s]?://[^>]+)>", r'<a href="\1">\1</a>', text)

	text = re.sub(r"\*(.*?)\*", r"<strong>\1</strong>", text)
	text = re.sub(r"(?<!\w)_(.*?)_(?!\w)", r"<em>\1</em>", text)
	text = re.sub(r"~(.*?)~", r"<del>\1</del>", text)
	text = re.sub(r"`(.*?)`", r"<code>\1</code>", text)

	text = re.sub(r"^\s*-\s+(.*)", r"<li>\1</li>", text, flags=re.MULTILINE)
	text = re.sub(r"(<li>.*?</li>)", r"<ul>\1</ul>", text, flags=re.DOTALL)
	text = re.sub(r"\n", r"<br>", text)
	return text

RE_MENTION = re.compile(r"<@([A-Z0-9]+)>")
def slackparse(text, users, user_mention_template):
	def resolve_mention(match):
		user_id = match.group(1)
		if user_id not in users:
			return match.group(0)
		user = users[user_id]
		return user_mention_template.render(user=user)
	_ = text or ""
	_ = emoji.emojize(_, language="alias")
	_ = re.sub(r":([a-z0-9_]+):", r'<span class="emoji">:\1:</span>', _)
	_ = slack2html(_)
	_ = RE_MENTION.sub(resolve_mention, _)
	return _

def main():
	parser = argparse.ArgumentParser(description="Render a slackdump archive as a static HTML website")
	parser.add_argument("db", help="Path to the slackdump archive SQLite database file")
	parser.add_argument("-p", "--only-public", action="store_true", help="Exclude private messages from the output")
	parser.add_argument("-c", "--channels", nargs="+", help="Render only the specified channels (by name or ID)")
	args = parser.parse_args()

	db_path = args.db
	only_public = args.only_public
	allowed_channels = args.channels

	outputdir = os.path.join(os.path.dirname(db_path), "html")
	channels, users = load_from_db(db_path, outputdir, only_public, allowed_channels)

	if not os.path.exists(outputdir):
		os.makedirs(outputdir)
	
	templatedir = os.path.join(os.path.dirname(__file__), "templates")
	env = Environment(
		loader=FileSystemLoader(templatedir),
		autoescape=select_autoescape(),
	)
	user_mention_template = env.get_template("user-mention.html.j2")
	env.filters["slackparse"] = lambda v: slackparse(v, users, user_mention_template)
	env.filters["safe_url"] = lambda v: quote(normalize("NFC", v), safe="/")
	channel_template = env.get_template("channel.html.j2")

	for channel in tqdm(channels.values(), desc="Rendering"):
		path = os.path.join(outputdir, f"{channel.slug}.html")
		
		# Render the channel page
		with open(path, "w", encoding="utf-8") as f:
			# Prepare messages for rendering, in timestamp order, respecting parent-child relationships
			messages = []
			messageids = set()
			for message in channel.messages:
				if message.id in messageids:
					continue
				messageids.add(message.id)
				messages.append(message)
				for child in message.children:
					if child.id not in messageids:
						messageids.add(child.id)
						messages.append(child)

			f.write(channel_template.render(channel=channel, messages=messages))

if __name__ == "__main__":
	main()
