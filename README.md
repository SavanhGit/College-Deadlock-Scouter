# 🎯 Collegiate Deadlock Scouting Report

A Streamlit web application that scrapes conference opponent schedules, resolves player profiles to Steam 32-bit Account IDs via Statlocker, and pulls live telemetry (rank badge, MMR/PP, win rates, top heroes) using the Deadlock API.

## Features
- **Auto-Roster Discovery:** Scrapes all opponents from a team's College Deadlock URL.
- **Player Telemetry:** Aggregates live hero pools, match volume, and rank data.
- **In-Memory Export:** Generates a formatted multi-sheet `.xlsx` file available for direct download.

## Local Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/](https://github.com/)<savanhgit>/deadlock-scout.git
   cd deadlock-scout