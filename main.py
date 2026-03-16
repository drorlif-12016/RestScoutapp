import streamlit as st
import requests
import pandas as pd
import json
import os

# Set up the Streamlit page
st.set_page_config(page_title="FTC Team Scouting Dashboard", page_icon="🤖", layout="wide")
st.title("FTC Team Scouting Dashboard")

# --- Constants & Configuration ---
BASE_URL = "https://api.ftcscout.org/rest/v1"
PROFILES_FILE = "team_profiles.json"

# --- Helper Functions ---
@st.cache_data(ttl=600)
def fetch_team_info(team_number):
    url = f"{BASE_URL}/teams/{team_number}"
    try:
        response = requests.get(url)
        return response.json() if response.status_code == 200 else None
    except:
        return None

@st.cache_data(ttl=600)
def fetch_team_events(team_number, season):
    url = f"{BASE_URL}/teams/{team_number}/events/{season}"
    try:
        response = requests.get(url)
        return response.json() if response.status_code == 200 else []
    except:
        return []

@st.cache_data(ttl=600)
def fetch_event_matches(season, event_code):
    url = f"{BASE_URL}/events/{season}/{event_code}/matches"
    try:
        response = requests.get(url)
        return response.json() if response.status_code == 200 else []
    except:
        return []

@st.cache_data(ttl=600)
def fetch_event_teams(season, event_code):
    url = f"{BASE_URL}/events/{season}/{event_code}/teams"
    try:
        response = requests.get(url)
        return response.json() if response.status_code == 200 else []
    except:
        return []

def get_last_event_before(events, target_event_code):
    """Finds the last event in chronological order before the target_event_code."""
    if not events:
        return None
    # Filter out the target event and any without stats
    valid_events = [e for e in events if e.get('eventCode') != target_event_code and e.get('stats')]
    if not valid_events:
        return None
    # Sort by updatedAt as a proxy for chronological order
    valid_events.sort(key=lambda x: x.get('updatedAt', ''), reverse=True)
    return valid_events[0]

def calculate_epa(matches, team_number, K=0.5):
    """
    EPA calculation based on matches in the event.
    Formula: delta = K * (alliance_score - predicted_alliance_EPA) / teams_per_alliance
    new_EPA = old_EPA + delta
    """
    epa = 0.0
    epa_history = []
    
    # Sort matches by ID/time to ensure chronological processing
    sorted_matches = sorted(matches, key=lambda x: x.get('actualStartTime', x.get('id', 0)))
    
    for match in sorted_matches:
        scores = match.get('scores', {})
        teams = match.get('teams', [])
        
        # Find which alliance the team was on
        team_entry = next((t for t in teams if t.get('teamNumber') == team_number), None)
        if not team_entry or not match.get('hasBeenPlayed'):
            continue
            
        alliance = team_entry.get('alliance')
        
        alliance_score = scores.get(alliance.lower(), {}).get('totalPointsNp', 0)
        
        # Prediction: use current EPA as prediction for the whole alliance
        # Assuming 2 teams per alliance
        predicted_alliance_EPA = epa * 2 
        
        teams_per_alliance = 2
        
        delta = K * (alliance_score - predicted_alliance_EPA) / teams_per_alliance
        epa += delta
        epa_history.append(epa)
        
    return epa, epa_history

def calculate_event_epas(matches, event_teams, K=0.5):
    """Calculates final EPA for all teams in the event based on matches, including breakdowns."""
    # Initialize all teams with 0.0 EPA for each component
    epas = {t.get('teamNumber'): {
        'total': 0.0,
        'auto': 0.0,
        'teleop': 0.0,
        'endgame': 0.0
    } for t in event_teams}
    
    # Sort matches chronologically
    sorted_matches = sorted(matches, key=lambda x: x.get('actualStartTime', x.get('id', 0)))
    
    for match in sorted_matches:
        if not match.get('hasBeenPlayed'):
            continue
            
        scores = match.get('scores', {})
        teams = match.get('teams', [])
        
        red_score_data = scores.get('red', {})
        blue_score_data = scores.get('blue', {})

        red_total = red_score_data.get('totalPointsNp', 0)
        blue_total = blue_score_data.get('totalPointsNp', 0)
        
        red_auto = red_score_data.get('autoPoints', 0)
        blue_auto = blue_score_data.get('autoPoints', 0)
        
        red_teleop = red_score_data.get('dcPoints', 0)
        blue_teleop = blue_score_data.get('dcPoints', 0)
        
        red_endgame = red_score_data.get('endgamePoints', 0)
        blue_endgame = blue_score_data.get('endgamePoints', 0)
        
        # If endgamePoints exists, we should subtract it from dcPoints to get pure teleop
        if 'endgamePoints' in red_score_data:
            red_teleop -= red_endgame
        if 'endgamePoints' in blue_score_data:
            blue_teleop -= blue_endgame

        red_teams = [t.get('teamNumber') for t in teams if t.get('alliance') == "Red"]
        blue_teams = [t.get('teamNumber') for t in teams if t.get('alliance') == "Blue"]
        
        if not red_teams or not blue_teams:
            continue

        # Helper to update component EPAs
        def update_component(comp_key, red_val, blue_val):
            pred_red = sum(epas.get(tn, {}).get(comp_key, 0.0) for tn in red_teams)
            pred_blue = sum(epas.get(tn, {}).get(comp_key, 0.0) for tn in blue_teams)
            
            delta_red = K * (red_val - pred_red) / len(red_teams)
            delta_blue = K * (blue_val - pred_blue) / len(blue_teams)
            
            for tn in red_teams:
                if tn in epas:
                    epas[tn][comp_key] += delta_red
            for tn in blue_teams:
                if tn in epas:
                    epas[tn][comp_key] += delta_blue

        update_component('total', red_total, blue_total)
        update_component('auto', red_auto, blue_auto)
        update_component('teleop', red_teleop, blue_teleop)
        update_component('endgame', red_endgame, blue_endgame)
                
    return epas

def load_profiles():
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_profile(team_number, profile_text):
    profiles = load_profiles()
    profiles[str(team_number)] = profile_text
    with open(PROFILES_FILE, 'w') as f:
        json.dump(profiles, f)

# --- Sidebar Inputs ---
with st.sidebar:
    st.header("Search Settings")
    team_num = st.number_input("Team Number", min_value=1, value=12016)
    season = st.number_input("Season", min_value=2019, max_value=2050, value=2026)
    target_event = st.text_input("Championship Event Code", value="ILCMP")
    
    highlight_opacity = st.slider("Highlight Opacity", min_value=0.0, max_value=1.0, value=1.0, step=0.05)
    
    if st.button("Refresh Data"):
        st.cache_data.clear()

# Fetch data
team_data = fetch_team_info(team_num)
all_events = fetch_team_events(team_num, season)
last_event = get_last_event_before(all_events, target_event)
target_event_data = next((e for e in all_events if e.get('eventCode') == target_event), None)

# --- Main Interface ---
if team_data:
    st.header(f"Team {team_num}: {team_data.get('name')}")
    st.write(f"**Location:** {team_data.get('city')}, {team_data.get('state')}, {team_data.get('country')}")
    
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Last Event Stats", 
        "🏆 Championship Stats",
        "📈 EPA Ranking", 
        "📝 Team Profile"
    ])
    
    # --- Tab 1: Last Event Stats ---
    with tab1:
        if last_event:
            st.subheader(f"Data from {last_event.get('eventCode')}")
            stats = last_event.get('stats') or {}
            
            # Key Stats
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Rank", stats.get('rank', 'N/A'))
            col2.metric("OPR", round(stats.get('opr', {}).get('totalPointsNp', 0), 2))
            col3.metric("Record", f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('ties', 0)}")
            col4.metric("Avg Score", round(stats.get('avg', {}).get('totalPoints', 0), 2))
            
            # More Detailed Stats
            st.markdown("### Performance Breakdown")
            detail_cols = st.columns(3)
            with detail_cols[0]:
                st.write("**Averages**")
                st.write(f"Auto AVG: {round(stats.get('avg', {}).get('autoPoints', 0), 2)}")
                st.write(f"Teleop AVG: {round(stats.get('avg', {}).get('dcPoints', 0), 2)}")
                st.write(f"Penalty AVG: {round(stats.get('avg', {}).get('penaltyPointsByOpp', 0), 2)}")
            with detail_cols[1]:
                st.write("**OPRs**")
                st.write(f"Auto OPR: {round(stats.get('opr', {}).get('autoPoints', 0), 2)}")
                st.write(f"Teleop OPR: {round(stats.get('opr', {}).get('dcPoints', 0), 2)}")
                st.write(f"NP OPR: {round(stats.get('opr', {}).get('totalPointsNp', 0), 2)}")
            with detail_cols[2]:
                st.write("**Other**")
                st.write(f"TBP: {stats.get('tb1', 'N/A')}")
                st.write(f"RS: {stats.get('rp', 'N/A')}")
                st.write(f"NP Max: {stats.get('max', {}).get('totalPointsNp', 'N/A')}")

            # Matches for Last Event
            st.markdown("### Match History")
            matches = fetch_event_matches(season, last_event.get('eventCode'))
            team_matches = [m for m in matches if any(t.get('teamNumber') == team_num for t in m.get('teams', []))]
            
            if team_matches:
                match_rows = []
                for m in team_matches:
                    scores = m.get('scores', {})
                    red_score = scores.get('red', {})
                    blue_score = scores.get('blue', {})
                    team_entry = next(t for t in m.get('teams') if t.get('teamNumber') == team_num)
                    alliance = team_entry.get('alliance')
                    
                    match_rows.append({
                        "Match": f"{m.get('tournamentLevel')} {m.get('id')}",
                        "Alliance": alliance,
                        "Total Points": red_score.get('totalPoints') if alliance == "Red" else blue_score.get('totalPoints'),
                        "Auto": red_score.get('autoPoints') if alliance == "Red" else blue_score.get('autoPoints'),
                        "Teleop": red_score.get('dcPoints') if alliance == "Red" else blue_score.get('dcPoints'),
                        "Penalty": red_score.get('penaltyPointsByOpp') if alliance == "Red" else blue_score.get('penaltyPointsByOpp'),
                        "Result": "W" if (red_score.get('totalPoints', 0) > blue_score.get('totalPoints', 0) and alliance == "Red") or 
                                       (blue_score.get('totalPoints', 0) > red_score.get('totalPoints', 0) and alliance == "Blue") else "L"
                    })
                st.table(pd.DataFrame(match_rows))
        else:
            st.info("No event data found prior to the target championship.")

    # --- Tab 2: ILCMP Stats ---
    with tab2:
        if target_event_data and target_event_data.get('stats'):
            st.subheader(f"Championship Performance: {target_event}")
            stats = target_event_data.get('stats') or {}
            
            # Key Stats (Copy-pasted logic from Tab 1 for same layout)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Rank", stats.get('rank', 'N/A'))
            col2.metric("OPR", round(stats.get('opr', {}).get('totalPointsNp', 0), 2))
            col3.metric("Record", f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('ties', 0)}")
            col4.metric("Avg Score", round(stats.get('avg', {}).get('totalPoints', 0), 2))
            
            # More Detailed Stats
            st.markdown("### Performance Breakdown")
            detail_cols = st.columns(3)
            with detail_cols[0]:
                st.write("**Averages**")
                st.write(f"Auto AVG: {round(stats.get('avg', {}).get('autoPoints', 0), 2)}")
                st.write(f"Teleop AVG: {round(stats.get('avg', {}).get('dcPoints', 0), 2)}")
                st.write(f"Penalty AVG: {round(stats.get('avg', {}).get('penaltyPointsByOpp', 0), 2)}")
            with detail_cols[1]:
                st.write("**OPRs**")
                st.write(f"Auto OPR: {round(stats.get('opr', {}).get('autoPoints', 0), 2)}")
                st.write(f"Teleop OPR: {round(stats.get('opr', {}).get('dcPoints', 0), 2)}")
                st.write(f"NP OPR: {round(stats.get('opr', {}).get('totalPointsNp', 0), 2)}")
            with detail_cols[2]:
                st.write("**Other**")
                st.write(f"TBP: {stats.get('tb1', 'N/A')}")
                st.write(f"RS: {stats.get('rp', 'N/A')}")
                st.write(f"NP Max: {stats.get('max', {}).get('totalPointsNp', 'N/A')}")

            # Matches for target Event
            st.markdown("### Match History")
            matches = fetch_event_matches(season, target_event)
            team_matches = [m for m in matches if any(t.get('teamNumber') == team_num for t in m.get('teams', []))]
            
            if team_matches:
                match_rows = []
                for m in team_matches:
                    scores = m.get('scores', {})
                    red_score = scores.get('red', {})
                    blue_score = scores.get('blue', {})
                    team_entry = next(t for t in m.get('teams') if t.get('teamNumber') == team_num)
                    alliance = team_entry.get('alliance')
                    
                    match_rows.append({
                        "Match": f"{m.get('tournamentLevel')} {m.get('id')}",
                        "Alliance": alliance,
                        "Total Points": red_score.get('totalPoints') if alliance == "Red" else blue_score.get('totalPoints'),
                        "Auto": red_score.get('autoPoints') if alliance == "Red" else blue_score.get('autoPoints'),
                        "Teleop": red_score.get('dcPoints') if alliance == "Red" else blue_score.get('dcPoints'),
                        "Penalty": red_score.get('penaltyPointsByOpp') if alliance == "Red" else blue_score.get('penaltyPointsByOpp'),
                        "Result": "W" if (red_score.get('totalPoints', 0) > blue_score.get('totalPoints', 0) and alliance == "Red") or 
                                       (blue_score.get('totalPoints', 0) > red_score.get('totalPoints', 0) and alliance == "Blue") else "L"
                    })
                st.table(pd.DataFrame(match_rows))
            else:
                st.info("No matches played yet at this event.")
        else:
            st.info(f"Team {team_num} hasn't competed at {target_event} or data is unavailable.")

    # --- Tab 3: EPA Ranking ---
    with tab3:
        st.subheader(f"EPA Rankings at {target_event}")
        
        # Fetch all teams and matches at the event
        event_teams = fetch_event_teams(season, target_event)
        event_matches = fetch_event_matches(season, target_event)
        
        if event_teams and event_matches:
            # Calculate EPAs for all teams
            all_epas = calculate_event_epas(event_matches, event_teams)
            
            # Prepare data for the ranking table
            ranking_data = []
            for t in event_teams:
                tn = t.get('teamNumber')
                team_epas = all_epas.get(tn, {'total': 0.0, 'auto': 0.0, 'teleop': 0.0, 'endgame': 0.0})
                
                # Fetch team's specific stats for record if available
                # Note: target_event_data is for the SELECTED team. We need it for ALL teams.
                # However, event_teams usually contains basic info.
                # To be efficient, we'll use the stats from event_teams if available.
                stats = t.get('stats') or {}
                record = f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('ties', 0)}"
                
                ranking_data.append({
                    "team number": tn,
                    "team name": t.get('name', 'Unknown'),
                    "EPA": round(team_epas.get('total', 0.0), 1),
                    "Auto EPA": round(team_epas.get('auto', 0.0), 1),
                    "Teleop EPA": round(team_epas.get('teleop', 0.0), 1),
                    "Endgame EPA": round(team_epas.get('endgame', 0.0), 1),
                    "Record": record
                })
            
            # Sort by EPA descending
            ranking_df = pd.DataFrame(ranking_data).sort_values(by="EPA", ascending=False).reset_index(drop=True)
            
            # Add Rank column at the beginning
            ranking_df.insert(0, 'Rank', ranking_df.index + 1)
            
            # Display the table
            def highlight_team(row):
                return [f'background-color: rgba(255, 255, 0, {highlight_opacity})' if row['team number'] == team_num else '' for _ in row]
            
            st.dataframe(ranking_df.style.apply(highlight_team, axis=1), use_container_width=True, hide_index=True)
            
        else:
            st.info(f"No team or match data available for {target_event} to generate rankings.")

    # --- Tab 4: Team Profile ---
    with tab4:
        st.subheader("Team Scout Profile")
        
        profiles = load_profiles()
        current_profile = profiles.get(str(team_num), "")
        
        # Real-time Edit
        new_profile = st.text_area("Edit Team Profile", value=current_profile, height=300)
        
        if st.button("Save Profile"):
            save_profile(team_num, new_profile)
            st.success("Profile saved successfully!")
            st.rerun()
            
        st.divider()
        st.markdown("### Profile Content")
        if current_profile:
            st.markdown(current_profile)
        else:
            st.write("No profile written yet.")
            
        # File Upload Option
        st.subheader("Upload Profile Document")
        uploaded_file = st.file_uploader("Upload a document", type=['txt', 'md'])
        if uploaded_file:
            # Check if the file has already been read in this session to avoid errors
            try:
                # Use string conversion to ensure we get text, or decode if bytes
                raw_data = uploaded_file.getvalue()
                content = raw_data.decode("utf-8")
                
                if st.button("Use Uploaded Content"):
                    save_profile(team_num, content)
                    st.success("Uploaded profile saved!")
                    st.rerun()
            except Exception as e:
                st.error(f"Error reading file: {e}")

else:
    st.error(f"Team {team_num} not found. Please check the team number.")

# Footer
st.divider()
st.caption("Data provided by FTCScout.org REST API")
