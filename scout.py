import base64
import io
import re
import time
from pathlib import Path
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://collegedeadlock.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CollegiateDeadlockScout/1.0"
}

session = requests.Session()
session.headers.update(HEADERS)

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

def format_deadlock_rank(rank_int):
    if not rank_int or not str(rank_int).isdigit():
        return "Unranked"
    rank_int = int(rank_int)
    if rank_int <= 0:
        return "Unranked"
    
    tier_idx = rank_int // 10
    subrank = rank_int % 10
    if 1 <= tier_idx < len(RANK_TIER_NAMES):
        return f"{RANK_TIER_NAMES[tier_idx]} {ROMAN.get(subrank, str(subrank))}"
    return f"Rank {rank_int}"

def get_opponent_teams(team_url, my_slug):
    res = session.get(team_url)
    soup = BeautifulSoup(res.text, "html.parser")
    opponents = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/teams/") and my_slug not in href:
            full_url = BASE_URL + href if not href.startswith("http") else href
            name = a.get_text(strip=True)
            if name and full_url not in [o["url"] for o in opponents]:
                opponents.append({"name": name, "url": full_url})
    return opponents

def get_team_players(team_url):
    res = session.get(team_url)
    soup = BeautifulSoup(res.text, "html.parser")
    players = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/players/"):
            player_name = a.get_text(strip=True)
            full_url = BASE_URL + href if not href.startswith("http") else href
            if player_name and full_url not in [p["url"] for p in players]:
                players.append({"name": player_name, "url": full_url})
    return players

def extract_account_id(player_url):
    try:
        res = session.get(player_url, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.find_all("a", href=True):
            if "statlocker.gg" in a["href"]:
                match = re.search(r"statlocker\.gg/(?:profile|account)/(\d+)", a["href"])
                if match:
                    return match.group(1), a["href"]
    except Exception:
        pass
    return None, "Not Found"

def fetch_live_stats(account_id):
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
            if rank_tier and subrank:
                stats["Rank"] = f"{RANK_TIER_NAMES[int(rank_tier)]} {ROMAN.get(int(subrank), subrank)}"
            elif badge_val is not None:
                stats["Rank"] = format_deadlock_rank(badge_val)
            final_progress = (b_data.get("last_match") or {}).get("player_rank_final_flat_progress")
            if final_progress is not None:
                stats["PP / MMR"] = final_progress
    except Exception:
        pass

    try:
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
                            stats["Rank"] = format_deadlock_rank(standard_badge)
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
                    h_name = HERO_ID_MAP.get(int(hid), f"Hero #{hid}")
                    wr = round((data["wins"] / data["games"]) * 100, 1) if data["games"] > 0 else 0
                    hero_summary.append(f"{h_name} ({data['games']}g, {wr}%)")

                stats["Top Heroes (Games / WR)"] = ", ".join(hero_summary)
    except Exception:
        pass

    return stats


# --- STREAMLIT UI ---
st.set_page_config(page_title="Collegiate Deadlock Scout", layout="wide")

DEFAULT_BACKGROUND_PATH = Path(__file__).with_name("hidden king.jpg")

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
    .sidebar-brand { font-family: 'Space Grotesk', sans-serif; font-size: 1.35rem; font-weight: 700; line-height: 1.05; }
    .sidebar-note { color: var(--muted); font-size: 0.84rem; line-height: 1.45; margin: 0.6rem 0 1.75rem; }
    .stButton > button[kind="primary"] {
        background: var(--accent); border: 0; color: #15191b; font-weight: 700;
        min-height: 3rem; border-radius: 6px;
    }
    .stButton > button[kind="primary"]:hover { background: var(--accent-dark); color: #15191b; }
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
</style>
""".replace(
    "__BACKGROUND_IMAGE__",
    "linear-gradient(135deg, #242a2d, #15191b 55%, #353b3f)",
), unsafe_allow_html=True)

st.markdown('<div class="eyebrow">COLLEGIATE DEADLOCK / SCOUTING TOOL</div>', unsafe_allow_html=True)
st.title("Opponent report, without the noise.")
st.markdown('<div class="subtitle">Scan rosters, rank context, match volume, and hero comfort picks in one focused report.</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="sidebar-brand">Deadlock<br>Scouter</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-note">Build a clear opponent snapshot from College Deadlock rosters and live player telemetry.</div>', unsafe_allow_html=True)
    st.markdown("### Report setup")
    team_input = st.text_input("Team URL or slug", value="utk-o", help="Paste a College Deadlock team URL or enter its slug.")
    start_btn = st.button("Generate report", type="primary", use_container_width=True)
    st.markdown('<div class="section-rule"></div>', unsafe_allow_html=True)
    st.caption("Data sources")
    st.caption("College Deadlock roster pages\n\nDeadlock API telemetry")

if start_btn and team_input:
    # Extract slug and build URL
    slug = team_input.strip().rstrip("/").split("/")[-1]
    team_url = f"{BASE_URL}/teams/{slug}"

    with st.status("Gathering opponent roster and player data...", expanded=True) as status:
        st.write(f"Scraping opponents from: `{team_url}`")
        opponents = get_opponent_teams(team_url, my_slug=slug)
        
        if not opponents:
            st.warning("No opponents found for this team URL. Verify the slug and try again.")
            status.update(label="Failed to find opponents", state="error")
        else:
            st.write(f"Found **{len(opponents)}** opponent teams.")
            progress_bar = st.progress(0)
            scouting_results = []

            for idx, opp in enumerate(opponents):
                st.write(f"🔍 Scouting team: **{opp['name']}**")
                players = get_team_players(opp["url"])

                for player in players:
                    account_id, sl_url = extract_account_id(player["url"])
                    if account_id:
                        stats = fetch_live_stats(account_id)
                    else:
                        stats = {
                            "Rank": "N/A", "PP / MMR": "N/A", "Win Rate (%)": "N/A",
                            "Matches": 0, "Top Heroes (Games / WR)": "N/A"
                        }

                    scouting_results.append({
                        "Opponent Team": opp["name"],
                        "Player": player["name"],
                        "Rank": stats["Rank"],
                        "PP / MMR": stats["PP / MMR"],
                        "Win Rate (%)": stats["Win Rate (%)"],
                        "Matches": stats["Matches"],
                        "Top Heroes (Games / WR)": stats["Top Heroes (Games / WR)"],
                        "Account ID": account_id or "Not Found",
                        "Statlocker URL": sl_url,
                        "Profile URL": player["url"]
                    })
                    time.sleep(0.1)

                progress_bar.progress((idx + 1) / len(opponents))

            status.update(label="Scouting Complete!", state="complete", expanded=False)

            # Display report
            df = pd.DataFrame(scouting_results)
            st.markdown('<div class="eyebrow">REPORT READY</div>', unsafe_allow_html=True)
            st.header("Scouting overview")

            ranked_players = df["Rank"].ne("Unranked").sum()
            average_matches = round(df["Matches"].mean(), 1) if not df.empty else 0
            metric_cols = st.columns(4)
            metric_cols[0].metric("Opponents", len(df["Opponent Team"].unique()))
            metric_cols[1].metric("Players", len(df))
            metric_cols[2].metric("Ranked players", int(ranked_players))
            metric_cols[3].metric("Avg. matches tracked", average_matches)

            overview_columns = [
                "Opponent Team", "Player", "Rank", "PP / MMR",
                "Win Rate (%)", "Matches", "Top Heroes (Games / WR)"
            ]
            details_columns = overview_columns + [
                "Account ID", "Statlocker URL", "Profile URL"
            ]

            overview_tab, details_tab, export_tab = st.tabs(["Overview", "Player details", "Export"])
            with overview_tab:
                st.dataframe(
                    df[overview_columns],
                    use_container_width=True,
                    hide_index=True,
                    height=520,
                    column_config={
                        "Opponent Team": st.column_config.TextColumn("Team", width="medium"),
                        "Player": st.column_config.TextColumn("Player", width="medium"),
                        "Rank": st.column_config.TextColumn("Rank", width="small"),
                        "PP / MMR": st.column_config.TextColumn("PP / MMR", width="small"),
                        "Win Rate (%)": st.column_config.TextColumn("Win rate", width="small"),
                        "Matches": st.column_config.NumberColumn("Matches", format="%d"),
                        "Top Heroes (Games / WR)": st.column_config.TextColumn("Comfort picks", width="large"),
                    },
                )
            with details_tab:
                st.dataframe(df[details_columns], use_container_width=True, hide_index=True, height=520)

            # Build Multi-Sheet Excel File in RAM
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="All Opponents", index=False)
                for team in df["Opponent Team"].unique():
                    team_df = df[df["Opponent Team"] == team]
                    safe_sheet = re.sub(r'[\\/*?:\[\]]', '', str(team))[:30]
                    team_df.to_excel(writer, sheet_name=safe_sheet, index=False)

            with export_tab:
                st.subheader("Take the report with you")
                st.write("Download the complete workbook with an all-opponents sheet and one sheet per team.")
                st.download_button(
                    label="Download scouting spreadsheet (.xlsx)",
                    data=excel_buffer.getvalue(),
                    file_name=f"deadlock_scout_{slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )