# 🎯 Collegiate Deadlock Scouting Report

A Streamlit web application that scrapes a College Deadlock team roster, resolves player profiles to Steam 32-bit Account IDs via Statlocker, and pulls live telemetry (rank badge, MMR/PP, win rates, top heroes) using the Deadlock API.

## Features
- **Team Scouting:** Scrapes every player from the submitted team's College Deadlock URL.
- **Player Telemetry:** Aggregates live hero pools, match volume, and rank data.
- **In-Memory Export:** Generates a formatted multi-sheet `.xlsx` file available for direct download.

## How to Use

Paste a full team URL such as `https://collegedeadlock.com/teams/example-team`, then select **Generate report**.

https://collegedeadlockscouter.streamlit.app/
