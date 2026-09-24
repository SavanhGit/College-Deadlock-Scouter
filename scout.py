import base64
import io
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://collegedeadlock.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollegiateDeadlockScout/1.0"
}

REQUEST_INTERVAL_SECONDS = 0.35
CACHE_TTL_SECONDS = 1800
CACHE_DATA = {}
REQUEST_LOCK = threading.Lock()
LAST_REQUEST_AT = 0.0


def enforce_request_limit():
    global LAST_REQUEST_AT
    with REQUEST_LOCK:
        now = time.monotonic()
        elapsed = now - LAST_REQUEST_AT
        if elapsed < REQUEST_INTERVAL_SECONDS:
            time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
        LAST_REQUEST_AT = time.monotonic()


def install_retry_session(sess):
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


session = requests.Session()
session.headers.update(HEADERS)
session = install_retry_session(session)

HERO_ASSETS_URL = "https://api.deadlock-api.com/v1/assets/heroes"

HERO_ID_MAP = {
    1: "Infernus", 2: "Seven", 3: "Vindicta", 4: "Lady Geist", 6: "Abrams",
    7: "Wraith", 8: "McGinnis", 10: "Paradox", 11: "Dynamo", 12: "Kelvin",
    13: "Haze", 14: "Hollis", 15: "Bebop", 17: "Grey Talon", 18: "Mo & Krill",
    19: "Shiv", 20: "Ivy", 25: "Warden", 27: "Yamato", 31: "Lash",
    35: "Viscous", 48: "Mirage", 50: "Pocket", 52: "Calico"
}

RANK_TIER_NAMES = [
    "Unranked", "Initiate", "Seeker", "Alchemist", "Arcanist", 
    "Ritualist", "Emissary", "Archon", "Oracle", "Phantom", "Ascendant", "Eternus"
]
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
ROMAN_VALUES = {value: key for key, value in ROMAN.items()}
ROLE_PRIORITY = {"Captain": 0, "Manager": 1, "Player": 2, "Sub": 3, "Coach": 4}
LINEUP_ROLES = {"Captain", "Player", "Sub"}
MAX_TEAM_SIZE = 6
MAX_SUBRANK = 6


def normalize_deadlock_rank(rank_int):
    if rank_int is None:
        return 0
    if isinstance(rank_int, str):
        rank_int = rank_int.strip()
        if not rank_int:
            return 0
        if rank_int.replace(".", "", 1).isdigit():
            rank_int = float(rank_int)
        else:
            try:
                rank_int = float(rank_int)
            except ValueError:
                return 0
    if isinstance(rank_int, float):
        if rank_int.is_integer():
            rank_int = int(rank_int)
        else:
            rank_int = int(round(rank_int))
    if not isinstance(rank_int, int):
        try:
            rank_int = int(rank_int)
        except (TypeError, ValueError):
            return 0
    if rank_int <= 0:
        return 0

    tier_idx = rank_int // 10
    subrank = rank_int % 10
    if tier_idx < 1:
        return 0
    if subrank < 1:
        return tier_idx * 10 + 1
    if subrank > MAX_SUBRANK:
        return tier_idx * 10 + MAX_SUBRANK
    return rank_int


def format_deadlock_rank(rank_int):
    rank_int = normalize_deadlock_rank(rank_int)
    if rank_int <= 0:
        return "Unranked"

    tier_idx = rank_int // 10
    subrank = rank_int % 10
    if 1 <= tier_idx < len(RANK_TIER_NAMES):
        return f"{RANK_TIER_NAMES[tier_idx]} {ROMAN.get(subrank, str(subrank))}"
    return f"Rank {rank_int}"

def rank_display_to_value(rank_display):
    if not isinstance(rank_display, str):
        return None
    rank_display = rank_display.strip()
    if not rank_display or rank_display == "Unranked":
        return None
    rank_display = rank_display.removesuffix(" (standard)")
    if rank_display.startswith("Rank "):
        try:
            return normalize_deadlock_rank(float(rank_display.removeprefix("Rank ")))
        except ValueError:
            return None
    for tier_idx, tier_name in enumerate(RANK_TIER_NAMES):
        prefix = f"{tier_name} "
        if rank_display.startswith(prefix):
            suffix = rank_display.removeprefix(prefix).strip()
            subrank = ROMAN_VALUES.get(suffix)
            if subrank is None:
                try:
                    subrank = int(suffix)
                except ValueError:
                    return None
            return normalize_deadlock_rank(tier_idx * 10 + subrank)
    return None


def format_average_rank(rank_value):
    if rank_value is None or pd.isna(rank_value):
        return "N/A"
    try:
        numeric_value = float(rank_value)
    except (TypeError, ValueError):
        return "N/A"
    normalized = normalize_deadlock_rank(numeric_value)
    tier_idx = normalized // 10
    if 1 <= tier_idx < len(RANK_TIER_NAMES):
        rank_name = format_deadlock_rank(normalized)
    else:
        rank_name = f"Rank {normalized}"
    return f"{rank_name} ({numeric_value:.1f})"

def get_cached_data(cache_key, factory, ttl_seconds=CACHE_TTL_SECONDS):
    now = time.time()
    entry = CACHE_DATA.get(cache_key)
    if entry and now - entry["timestamp"] < ttl_seconds:
        return entry["value"]
    value = factory()
    CACHE_DATA[cache_key] = {"value": value, "timestamp": now}
    return value


def fetch_html(url, timeout=8, headers=None):
    enforce_request_limit()
    try:
        response = session.get(url, timeout=timeout, headers=headers)
        if response.status_code == 429:
            time.sleep(2)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return None


def get_team_players(team_url):
    slug = team_url.rstrip("/").split("/")[-1]
    cache_key = f"players:{slug}"

    def _load_players():
        html = fetch_html(team_url)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        players = []
        for li in soup.select("li"):
            a = li.select_one("a[href^='/players/']")
            if not a:
                continue

            player_name = a.get_text(strip=True)
            full_url = BASE_URL + a["href"] if not a["href"].startswith("http") else a["href"]
            if not player_name or full_url in [p["url"] for p in players]:
                continue

            li_text = li.get_text(" ", strip=True)
            role = "Player"
            if "Captain" in li_text:
                role = "Captain"
            elif "Manager" in li_text:
                role = "Manager"
            elif "Sub" in li_text:
                role = "Sub"
            elif "Coach" in li_text:
                role = "Coach"

            players.append({"name": player_name, "url": full_url, "role": role})

        players.sort(key=lambda p: (ROLE_PRIORITY.get(p.get("role"), 99), p["name"].lower()))
        return players

    return get_cached_data(cache_key, _load_players)


def get_team_name(team_url):
    html = fetch_html(team_url)
    fallback_name = team_url.rstrip("/").split("/")[-1].replace("-", " ").title()
    if not html:
        return fallback_name

    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    return heading.get_text(" ", strip=True) if heading else fallback_name


def extract_account_id(player_url):
    try:
        html = fetch_html(player_url, timeout=8)
        if not html:
            return None, "Not Found"
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if "statlocker.gg" in a["href"]:
                match = re.search(r"statlocker\.gg/(?:profile|account)/(\d+)", a["href"])
                if match:
                    return match.group(1), a["href"]
    except Exception:
        pass
    return None, "Not Found"


def get_hero_names():
    def _load_hero_names():
        try:
            enforce_request_limit()
            response = session.get(HERO_ASSETS_URL, timeout=8)
            response.raise_for_status()
            assets = response.json()
            if isinstance(assets, list):
                return {
                    int(hero["id"]): hero["name"]
                    for hero in assets
                    if hero.get("id") is not None and hero.get("name")
                }
        except (requests.RequestException, TypeError, ValueError, KeyError):
            pass
        return HERO_ID_MAP.copy()

    return get_cached_data("heroes", _load_hero_names)
# Looks for our stats
def fetch_live_stats(account_id, hero_names):
    stats = {
        "Rank": "Unranked",
        "PP / MMR": "N/A",
        "Win Rate (%)": "N/A",
        "Matches": 0,
        "Top Heroes (Games / WR)": "N/A"
    }

    if not account_id or account_id in ("Unknown", "Not Found"):
        return stats

    try:
        enforce_request_limit()
        badge_res = requests.get(
            f"https://api.deadlock-api.com/v1/players/{account_id}/rank",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if badge_res.status_code == 200:
            b_data = badge_res.json()
            badge_val = b_data.get("badge")
            rank_tier = b_data.get("rank")
            subrank = b_data.get("subrank")
            if rank_tier is not None and subrank is not None:
                safe_rank = normalize_deadlock_rank((int(rank_tier) * 10) + int(subrank))
                stats["Rank"] = format_deadlock_rank(safe_rank)
            elif badge_val is not None:
                stats["Rank"] = format_deadlock_rank(badge_val)
            final_progress = (b_data.get("last_match") or {}).get("player_rank_final_flat_progress")
            if final_progress is not None:
                stats["PP / MMR"] = final_progress
    except Exception:
        pass

    try:
        enforce_request_limit()
        mmr_res = requests.get(
            f"https://api.deadlock-api.com/v1/players/{account_id}/mmr-history",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if mmr_res.status_code == 200:
            mmr_history = mmr_res.json()
            if isinstance(mmr_history, list) and mmr_history:
                latest_mmr = mmr_history[-1]
                pp_value = latest_mmr.get("player_score")
                if pp_value is not None and stats["PP / MMR"] == "N/A":
                    stats["PP / MMR"] = pp_value
    except Exception:
        pass

    try:
        enforce_request_limit()
        hist_res = requests.get(
            f"https://api.deadlock-api.com/v1/players/{account_id}/match-history?only_stored_history=true",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if hist_res.status_code == 200:
            matches = hist_res.json()
            if isinstance(matches, list) and len(matches) > 0:
                if stats["Rank"] == "Unranked":
                    for match in reversed(matches):
                        standard_badge = match.get("ranked_display_badge")
                        if standard_badge:
                            stats["Rank"] = f"{format_deadlock_rank(standard_badge)} (standard)"
                            break

                total_matches = len(matches)
                wins = 0
                hero_tally = {}

                for m in matches:
                    is_win = (
                        m.get("player_result") == 1
                        or m.get("player_match_outcome") == 1
                        or m.get("match_result") == 1
                        or m.get("won") is True
                    )
                    if is_win:
                        wins += 1

                    hid = m.get("hero_id")
                    if hid:
                        if hid not in hero_tally:
                            hero_tally[hid] = {"games": 0, "wins": 0}
                        hero_tally[hid]["games"] += 1
                        if is_win:
                            hero_tally[hid]["wins"] += 1

                stats["Matches"] = total_matches
                stats["Win Rate (%)"] = f"{round((wins / total_matches) * 100, 1)}%"

                sorted_heroes = sorted(hero_tally.items(), key=lambda item: item[1]["games"], reverse=True)[:3]
                hero_summary = []
                for hid, data in sorted_heroes:
                    h_name = hero_names.get(int(hid), f"Hero #{hid}")
                    wr = round((data["wins"] / data["games"]) * 100, 1) if data["games"] > 0 else 0
                    hero_summary.append(f"{h_name} ({data['games']}g, {wr}%)")

                stats["Top Heroes (Games / WR)"] = ", ".join(hero_summary)
    except Exception:
        pass

    return stats


# --- STREAMLIT UI ---
st.set_page_config(page_title="Collegiate Deadlock Scout", layout="wide")

DEFAULT_BACKGROUND_PATH = Path(__file__).with_name("hidden king.jpg")
ICON_PATH = Path(__file__).with_name("icon.png")
ICON_DATA_URI = (
    f"data:image/png;base64,{base64.b64encode(ICON_PATH.read_bytes()).decode('ascii')}"
    if ICON_PATH.exists()
    else ""
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

    :root {
        --ink: #edf1f2;
        --muted: #aeb8bc;
        --paper: #15191b;
        --panel: rgba(35, 41, 44, 0.92);
        --line: rgba(215, 222, 225, 0.18);
        --accent: #c7cdd0;
        --accent-dark: #929ba0;
    }

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
        color: var(--ink);
    }

    [data-testid="stAppViewContainer"] {
        background: transparent;
        isolation: isolate;
    }

    [data-testid="stApp"] { background: transparent; }

    [data-testid="stAppViewContainer"]::before {
        content: "";
        position: fixed;
        inset: 0;
        z-index: -2;
        background-image: __BACKGROUND_IMAGE__;
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
    }

    [data-testid="stAppViewContainer"]::after {
        content: "";
        position: fixed;
        inset: 0;
        z-index: -1;
        background: rgba(10, 13, 15, 0.74);
        backdrop-filter: grayscale(0.7) blur(3px);
    }

    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] { background: rgba(22, 26, 28, 0.94); border-right: 1px solid var(--line); }
    [data-testid="stSidebar"] > div:first-child { padding-top: 2rem; }
    [data-testid="stMainBlockContainer"] {
        max-width: 1400px;
        padding-top: 2.5rem;
        background: rgba(21, 25, 27, 0.82);
        border: 1px solid var(--line);
        border-radius: 12px;
        box-shadow: 0 18px 60px rgba(0, 0, 0, 0.38);
    }

    h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; letter-spacing: 0; color: var(--ink); }
    h1 { font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1.02; margin-bottom: 0.5rem; }
    h2 { margin-top: 1.5rem; }

    .eyebrow {
        color: var(--accent); font-size: 0.76rem; font-weight: 700;
        letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 0.75rem;
    }
    .subtitle { color: var(--muted); font-size: 1.05rem; max-width: 680px; margin-bottom: 2rem; }
    .section-rule { border-top: 1px solid var(--line); margin: 1.5rem 0 1rem; }
    .sidebar-brand { display: flex; align-items: center; gap: 0.65rem; font-family: 'Space Grotesk', sans-serif; font-size: 1.35rem; font-weight: 700; line-height: 1.05; }
    .sidebar-brand img { width: 2.4rem; height: 2.4rem; object-fit: contain; flex: 0 0 auto; }
    .sidebar-note { color: var(--muted); font-size: 0.84rem; line-height: 1.45; margin: 0.6rem 0 1.75rem; }
    .sidebar-footer {
        margin-top: auto; padding-top: 1.25rem; border-top: 1px solid var(--line);
        color: var(--muted); font-size: 0.76rem; line-height: 1.5;
    }
    .sidebar-link {
        display: inline-block; margin-top: 0.35rem; color: var(--ink); text-decoration: none;
        font-weight: 600; border: 1px solid var(--line); border-radius: 999px; padding: 0.4rem 0.7rem;
        background: rgba(255,255,255,0.03);
    }
    .sidebar-link:hover { border-color: var(--accent); }
    .stButton > button[kind="primary"] {
        background: #252b2e; border: 1px solid #6f797e; color: var(--ink); font-weight: 700;
        min-height: 3rem; border-radius: 6px;
    }
    .stButton > button[kind="primary"]:hover { background: #343c40; border-color: #aeb8bc; color: var(--ink); }
    [data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--line); padding: 1rem; border-radius: 6px; }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
    [data-testid="stFileUploader"] { color: var(--ink); background: rgba(35, 41, 44, 0.62); border-radius: 6px; padding: 0.35rem; }
    [data-testid="stWidgetLabel"] p, [data-testid="stCaptionContainer"] p { color: var(--muted); }
    [data-baseweb="input"], [data-baseweb="select"] > div { background: #252b2e; border-color: var(--line); color: var(--ink); }
    [data-baseweb="input"] input { color: var(--ink); }
    [data-baseweb="input"] input::placeholder { color: #879195; }
    [data-baseweb="tab-list"] { gap: 0.35rem; border-bottom-color: var(--line); }
    [data-baseweb="tab"] { color: var(--muted); }
    [data-baseweb="tab"][aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
    [data-testid="stAlert"] { background: rgba(35, 41, 44, 0.9); color: var(--ink); border-color: var(--line); }
    [data-testid="stStatusWidget"] { background: rgba(35, 41, 44, 0.9); border-color: var(--line); }
    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { color: var(--ink); }
    [data-testid="stMetricValue"] { color: var(--ink); }
    [data-testid="stMetricDelta"] { color: var(--muted); }
    .roster-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 0.75rem; padding: 0.5rem 0.75rem; margin: 0.2rem 0;
        border: 1px solid var(--line); border-radius: 8px; background: rgba(35, 41, 44, 0.55);
    }
    .roster-name { color: var(--ink); font-weight: 500; }
    .role-badge {
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 72px; padding: 0.2rem 0.55rem; border-radius: 999px; font-size: 0.62rem;
        font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; border: 1px solid var(--line);
    }
    .role-captain { color: #f2d38a; border-color: rgba(242, 211, 138, 0.6); background: rgba(242, 211, 138, 0.08); }
    .role-player { color: var(--ink); border-color: rgba(215, 222, 225, 0.34); background: rgba(215, 222, 225, 0.05); }
    .role-sub { color: #b7d6ff; border-color: rgba(183, 214, 255, 0.45); background: rgba(183, 214, 255, 0.08); }
    .role-coach { color: #d9a7ff; border-color: rgba(217, 167, 255, 0.5); background: rgba(217, 167, 255, 0.08); }
    .role-manager { color: #8fe3c0; border-color: rgba(143, 227, 192, 0.5); background: rgba(143, 227, 192, 0.08); }
</style>
""".replace(
    "__BACKGROUND_IMAGE__",
    "linear-gradient(135deg, #242a2d, #15191b 55%, #353b3f)",
), unsafe_allow_html=True)

st.markdown('<div class="eyebrow">COLLEGIATE DEADLOCK SCOUTING TOOL</div>', unsafe_allow_html=True)
st.title("Team scouting report")
st.markdown('<div class="subtitle">Scan a team roster, rank context, match volume, and hero comfort picks in one focused report.</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f'<div class="sidebar-brand"><img src="{ICON_DATA_URI}" alt=""><span>Deadlock<br>Scouter</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-note">Build a clear team snapshot from College Deadlock rosters and live player telemetry.</div>', unsafe_allow_html=True)
    st.markdown("### Report setup")
    team_input = st.text_input("Team URL", value="", help="Paste a College Deadlock team URL")
    start_btn = st.button("Generate report", type="primary", use_container_width=True)
    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    st.caption("Data sources")
    st.caption("College Deadlock roster pages\n\nDeadlock API telemetry")
    st.markdown(
        '<div class="sidebar-footer">'
        '<div>Built for scouting coverage and quick team reads.</div>'
        '<a class="sidebar-link" href="https://github.com/SavanhGit/College-Deadlock-Scouter" target="_blank" rel="noopener noreferrer">★ Star the repo</a>'
        '</div>',
        unsafe_allow_html=True,
    )

if start_btn and team_input:
    team_url = team_input.strip()
    parsed_url = urlparse(team_url)
    valid_team_url = (
        parsed_url.scheme in {"http", "https"}
        and parsed_url.netloc == urlparse(BASE_URL).netloc
        and parsed_url.path.rstrip("/").startswith("/teams/")
        and bool(parsed_url.path.rstrip("/").split("/")[-1])
    )
    slug = parsed_url.path.rstrip("/").split("/")[-1] if valid_team_url else "team"

    with st.status("Gathering team roster and player data...", expanded=True) as status:
        if not valid_team_url:
            st.warning(f"Enter a full College Deadlock team URL, such as `{BASE_URL}/teams/example-team`.")
            status.update(label="Invalid team URL", state="error")
            st.stop()
        else:
            st.write(f"Scraping team roster from: `{team_url}`")
            team_name = get_team_name(team_url)
            players = get_team_players(team_url)

            if not players:
                st.warning("No player links were found on this team's roster page. Verify the URL or try again later.")
                status.update(label="Failed to find team players", state="error")
                st.stop()
            else:
                st.write(f"Found **{len(players)}** players on **{team_name}**.")
                progress_bar = st.progress(0)
                scouting_results = []
                hero_names = get_hero_names()

                for idx, player in enumerate(players):
                    st.write(f"Scouting player: **{player['name']}**")
                    try:
                        account_id, sl_url = extract_account_id(player["url"])
                        if account_id:
                            stats = fetch_live_stats(account_id, hero_names)
                        else:
                            stats = {
                                "Rank": "N/A", "PP / MMR": "N/A", "Win Rate (%)": "N/A",
                                "Matches": 0, "Top Heroes (Games / WR)": "N/A"
                            }
                    except Exception:
                        account_id, sl_url = None, "Not Found"
                        stats = {
                            "Rank": "N/A", "PP / MMR": "N/A", "Win Rate (%)": "N/A",
                            "Matches": 0, "Top Heroes (Games / WR)": "N/A"
                        }

                    scouting_results.append({
                        "Team": team_name,
                        "Player": player["name"],
                        "Role": player.get("role", "Player"),
                        "Role Order": ROLE_PRIORITY.get(player.get("role", "Player"), 99),
                        "Rank": stats["Rank"],
                        "PP / MMR": stats["PP / MMR"],
                        "Win Rate (%)": stats["Win Rate (%)"],
                        "Matches": stats["Matches"],
                        "Top Heroes (Games / WR)": stats["Top Heroes (Games / WR)"],
                        "Account ID": account_id or "Not Found",
                        "Statlocker URL": sl_url,
                        "Profile URL": player["url"]
                    })
                    progress_bar.progress((idx + 1) / len(players))
                    time.sleep(0.1)

            # Display report
            df = pd.DataFrame(scouting_results)
            st.markdown('<div class="eyebrow">REPORT READY</div>', unsafe_allow_html=True)
            st.header("Scouting overview")

            rank_values = df["Rank"].map(rank_display_to_value)
            ranked_players = rank_values.notna().sum()
            average_matches = round(df["Matches"].mean(), 1) if not df.empty else 0
            metric_cols = st.columns(4)
            metric_cols[0].metric("Team", team_name)
            metric_cols[1].metric("Players", len(df))
            metric_cols[2].metric("Ranked players", int(ranked_players))
            metric_cols[3].metric("Avg. matches tracked", average_matches)

            team_rank_rows = []
            for team, team_df in df.groupby("Team", sort=False):
                lineup_df = team_df[team_df["Role"].isin(LINEUP_ROLES)].copy()
                lineup_df = lineup_df.sort_values(["Role Order", "Player"], kind="mergesort").head(MAX_TEAM_SIZE)
                team_rank_values = lineup_df["Rank"].map(rank_display_to_value).dropna()
                team_rank_rows.append({
                    "Team": team,
                    "Players": len(lineup_df),
                    "Ranked players": len(team_rank_values),
                    "Team rank estimate": (
                        format_average_rank(team_rank_values.mean())
                        if not team_rank_values.empty else "N/A"
                    ),
                })
            team_rank_df = pd.DataFrame(team_rank_rows)
            st.subheader("Team rank estimates")
            st.caption("Average rank across this team's ranked players.")
            st.dataframe(
                team_rank_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Team": st.column_config.TextColumn("Team", width="large"),
                    "Players": st.column_config.NumberColumn("Players", format="%d"),
                    "Ranked players": st.column_config.NumberColumn("Ranked players", format="%d"),
                    "Team rank estimate": st.column_config.TextColumn("Overall rank estimate", width="medium"),
                },
            )

            overview_columns = [
                "Team", "Player", "Role", "Rank", "PP / MMR",
                "Win Rate (%)", "Matches", "Top Heroes (Games / WR)"
            ]
            details_columns = overview_columns + [
                "Account ID", "Statlocker URL", "Profile URL"
            ]

            overview_tab, details_tab, export_tab = st.tabs(["Overview", "Player details", "Export"])
            with overview_tab:
                df_sorted = (
                    df.assign(_team_order=0)
                    .sort_values(["_team_order", "Role Order", "Player"], kind="mergesort")
                    .drop(columns="_team_order")
                    .reset_index(drop=True)
                )
                st.dataframe(
                    df_sorted[overview_columns],
                    use_container_width=True,
                    hide_index=True,
                    height=520,
                    column_config={
                        "Team": st.column_config.TextColumn("Team", width="medium"),
                        "Player": st.column_config.TextColumn("Player", width="medium"),
                        "Role": st.column_config.TextColumn("Role", width="small"),
                        "Rank": st.column_config.TextColumn("Rank", width="small"),
                        "PP / MMR": st.column_config.TextColumn("PP / MMR", width="small"),
                        "Win Rate (%)": st.column_config.TextColumn("Win rate", width="small"),
                        "Matches": st.column_config.NumberColumn("Matches", format="%d"),
                        "Top Heroes (Games / WR)": st.column_config.TextColumn("Comfort picks", width="large"),
                    },
                )
            with details_tab:
                roster_groups = df.sort_values(["Team", "Role Order", "Player"], kind="mergesort").groupby("Team", sort=False)
                for team_name, team_df in roster_groups:
                    st.markdown(f"### {team_name}")
                    for _, row in team_df.iterrows():
                        role = row["Role"]
                        role_class = f"role-{role.lower()}" if role.lower() in {"captain", "manager", "player", "sub", "coach"} else "role-player"
                        st.markdown(
                            f'<div class="roster-row"><span class="roster-name">{row["Player"]}</span><span class="role-badge {role_class}">{role}</span></div>',
                            unsafe_allow_html=True,
                        )
                st.dataframe(df[details_columns], use_container_width=True, hide_index=True, height=520)

            # Build Multi-Sheet Excel File in RAM
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                team_rank_df.to_excel(writer, sheet_name="Team Rank Estimates", index=False)
                df.to_excel(writer, sheet_name="Team Players", index=False)
                for team in df["Team"].unique():
                    team_df = df[df["Team"] == team]
                    safe_sheet = re.sub(r'[\\/*?:\[\]]', '', str(team))[:30]
                    team_df.to_excel(writer, sheet_name=safe_sheet, index=False)

            with export_tab:
                st.subheader("Take the report with you")
                st.write("Download the complete workbook with the team roster and player details.")
                st.download_button(
                    label="Download scouting spreadsheet (.xlsx)",
                    data=excel_buffer.getvalue(),
                    file_name=f"deadlock_scout_{slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )

            status.update(label="Scouting Complete!", state="complete", expanded=False)