import re
from collections import defaultdict
from datetime import datetime

import requests
import streamlit as st
from bs4 import BeautifulSoup


SEASON_URL = "https://collegedeadlock.com/events/season-3"
HEADERS = {"User-Agent": "DeadlockPickem/1.0"}


@st.cache_data(ttl=900)
def load_schedule():
	response = requests.get(SEASON_URL, headers=HEADERS, timeout=15)
	response.raise_for_status()
	season = BeautifulSoup(response.text, "html.parser")
	division_links = [
		link
		for link in season.find_all("a", href=True)
		if re.fullmatch(r"/events/season-3-division-\d+", link["href"])
	]
	weeks = defaultdict(lambda: defaultdict(list))

	for division_link in division_links:
		division_url = requests.compat.urljoin(SEASON_URL, division_link["href"])
		division_response = requests.get(division_url, headers=HEADERS, timeout=15)
		division_response.raise_for_status()
		division_page = BeautifulSoup(division_response.text, "html.parser")
		division_heading = division_page.find("h1")
		if not division_heading:
			continue
		division_name = division_heading.get_text(" ", strip=True)
		upcoming_heading = division_page.find(
			lambda tag: tag.name in ("h2", "h3")
			and tag.get_text(" ", strip=True) == "Upcoming Matches"
		)
		if not upcoming_heading:
			continue

		match_section = upcoming_heading.find_parent("section")
		for card in match_section.select("div.card"):
			team_links = card.find_all("a", href=lambda href: href and href.startswith("/teams/"))
			match_link = card.find("a", href=lambda href: href and "/matches/" in href)
			week_match = re.search(r"Week\s+(\d+)", card.get_text(" ", strip=True))
			if len(team_links) != 2 or not match_link or not week_match:
				continue

			date_match = re.search(
				r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+\d{1,2}:\d{2}\s+[AP]M\s+[A-Z]{3}",
				card.get_text(" ", strip=True),
			)
			date = date_match.group(1) if date_match else "Date TBD"
			try:
				parsed_date = datetime.strptime(date, "%b %d, %Y")
				date = f"{parsed_date:%A}, {parsed_date:%b} {parsed_date.day}"
			except ValueError:
				pass

			week_name = f"Week {week_match.group(1)}"
			match_id = match_link["href"].rstrip("/").split("/")[-1]
			weeks[week_name][division_name].append(
				{
					"id": f"season-3-{match_id}",
					"date": date,
					"away": {"name": team_links[0].select_one("div").get_text(" ", strip=True)},
					"home": {"name": team_links[1].select_one("div").get_text(" ", strip=True)},
				}
			)

	return {week: dict(divisions) for week, divisions in weeks.items()}


def team_label(team):
	return team["name"]


def render_game(game):
	st.markdown(f"**{game['date']}**")
	teams = [team_label(game[side]) for side in ("away", "home")]
	current_pick = st.session_state.picks.get(game["id"])
	selected_pick = st.radio(
		"Pick the winner",
		teams,
		index=teams.index(current_pick) if current_pick in teams else None,
		key=game["id"],
		label_visibility="collapsed",
	)
	if selected_pick:
		st.session_state.picks[game["id"]] = selected_pick


st.set_page_config(page_title="Deadlock Pick'em", page_icon="🏆", layout="wide")

if "picks" not in st.session_state:
	st.session_state.picks = {}

try:
	weeks = load_schedule()
except requests.RequestException as error:
	st.error(f"Could not load the Season 3 schedule: {error}")
	st.stop()

if not weeks:
	st.warning("No upcoming Season 3 matches were found.")
	st.stop()

st.title("Deadlock Pick'em")
st.caption("Choose one winner for every game before the weekly lock time.")

week_name = st.selectbox("Week", list(weeks), label_visibility="collapsed")
week_games = weeks[week_name]
division_order = list(week_games)
all_games = [game for division in division_order for game in week_games.get(division, [])]
completed_picks = sum(game["id"] in st.session_state.picks for game in all_games)

summary_col, action_col = st.columns([3, 1])
with summary_col:
	st.metric("Weekly picks", f"{completed_picks} / {len(all_games)}")
with action_col:
	if st.button("Clear this week", use_container_width=True):
		for game in all_games:
			st.session_state.picks.pop(game["id"], None)
		st.rerun()

for division in division_order:
	games = week_games.get(division, [])
	if not games:
		continue

	st.subheader(f"{division} Division")
	for game in games:
		with st.container(border=True):
			render_game(game)

st.divider()
if completed_picks == len(all_games):
	if st.button("Submit weekly picks", type="primary", use_container_width=True):
		st.success(f"{week_name} picks submitted. Good luck!")
else:
	st.info(f"Make {len(all_games) - completed_picks} more pick(s) before submitting.")