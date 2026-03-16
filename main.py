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
                data = json.load(f)
                # Migration: if data is old format (team_num: string), convert to (team_num: [string])
                updated = False
                for team_id in data:
                    if isinstance(data[team_id], str):
                        data[team_id] = [{"name": "Default Profile", "content": data[team_id]}]
                        updated = True
                if updated:
                    save_all_profiles(data)
                return data
        except:
            return {}
    return {}

def save_all_profiles(profiles):
    with open(PROFILES_FILE, 'w') as f:
        json.dump(profiles, f, indent=4)

def save_profile(team_number, profile_index, profile_name, profile_content):
    profiles = load_profiles()
    team_id = str(team_number)
    if team_id not in profiles:
        profiles[team_id] = []
    
    new_profile = {"name": profile_name, "content": profile_content}
    
    if profile_index is None:
        profiles[team_id].append(new_profile)
    elif 0 <= profile_index < len(profiles[team_id]):
        profiles[team_id][profile_index] = new_profile
    
    save_all_profiles(profiles)

def delete_profile(team_number, profile_index):
    profiles = load_profiles()
    team_id = str(team_number)
    if team_id in profiles and 0 <= profile_index < len(profiles[team_id]):
        profiles[team_id].pop(profile_index)
        save_all_profiles(profiles)

# --- Sidebar Inputs ---
with st.sidebar:
    st.header("Search Settings")
    team_num = st.number_input("Team Number", min_value=1, value=12016)
    season = st.number_input("Season", min_value=2019, max_value=2050, value=2025)
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
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Last Event Stats", 
        "🏆 Championship Stats",
        "📈 EPA Ranking", 
        "🥇 OPR Ranking",
        "📝 Team Profile"
    ])
    
    # --- Tab 1: Last Event Stats ---
    with tab1:
        if last_event:
            st.subheader(f"Data from {last_event.get('eventCode')}")
            stats = last_event.get('stats') or {}
            
            # Key Stats
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Rank", stats.get('rank', 'N/A'))
            
            # Fetch matches and teams for the last event to calculate EPA
            last_event_code = last_event.get('eventCode')
            last_event_matches = fetch_event_matches(season, last_event_code)
            last_event_teams = fetch_event_teams(season, last_event_code)
            last_event_epas = calculate_event_epas(last_event_matches, last_event_teams)
            team_last_epa = last_event_epas.get(team_num, {'total': 0.0, 'auto': 0.0, 'teleop': 0.0, 'endgame': 0.0})
            
            col2.metric("EPA", round(team_last_epa.get('total', 0.0), 1))
            col3.metric("OPR", round(stats.get('opr', {}).get('totalPointsNp', 0), 2))
            col4.metric("Record", f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('ties', 0)}")
            col5.metric("Avg Score", round(stats.get('avg', {}).get('totalPoints', 0), 2))
            
            # More Detailed Stats
            st.markdown("### Performance Breakdown")
            detail_cols = st.columns(4)
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
                st.write("**EPAs**")
                st.write(f"Auto EPA: {round(team_last_epa.get('auto', 0.0), 1)}")
                st.write(f"Teleop EPA: {round(team_last_epa.get('teleop', 0.0), 1)}")
                st.write(f"Endgame EPA: {round(team_last_epa.get('endgame', 0.0), 1)}")
            with detail_cols[3]:
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

    # --- Tab 4: OPR Ranking ---
    with tab4:
        st.subheader(f"OPR Rankings at {target_event}")
        
        # Fetch all teams at the event
        event_teams = fetch_event_teams(season, target_event)
        
        if event_teams:
            # Prepare data for the OPR ranking table
            opr_ranking_data = []
            for t in event_teams:
                tn = t.get('teamNumber')
                stats = t.get('stats') or {}
                opr_stats = stats.get('opr', {})
                
                record = f"{stats.get('wins', 0)}-{stats.get('losses', 0)}-{stats.get('ties', 0)}"
                
                opr_ranking_data.append({
                    "team number": tn,
                    "team name": t.get('name', 'Unknown'),
                    "OPR": round(opr_stats.get('totalPointsNp', 0.0), 1),
                    "Auto OPR": round(opr_stats.get('autoPoints', 0.0), 1),
                    "Teleop OPR": round(opr_stats.get('dcPoints', 0.0), 1),
                    "NP Max": stats.get('max', {}).get('totalPointsNp', 0),
                    "Record": record
                })
            
            # Sort by OPR descending
            opr_ranking_df = pd.DataFrame(opr_ranking_data).sort_values(by="OPR", ascending=False).reset_index(drop=True)
            
            # Add Rank column at the beginning
            opr_ranking_df.insert(0, 'Rank', opr_ranking_df.index + 1)
            
            # Display the table
            def highlight_team_opr(row):
                return [f'background-color: rgba(255, 255, 0, {highlight_opacity})' if row['team number'] == team_num else '' for _ in row]
            
            st.dataframe(opr_ranking_df.style.apply(highlight_team_opr, axis=1), use_container_width=True, hide_index=True)
            
        else:
            st.info(f"No team data available for {target_event} to generate OPR rankings.")

    # --- Tab 5: Team Profile ---
    with tab5:
        st.subheader("Team Scout Profile")
        
        profiles_list = load_profiles().get(str(team_num), [])
        
        # Profile Management
        if not profiles_list:
            st.info("No profiles written yet.")
            if st.button("Create First Profile"):
                save_profile(team_num, None, "Default Profile", "")
                st.rerun()
        else:
            profile_names = [p.get('name', 'Unnamed') for p in profiles_list]
            selected_idx = st.selectbox("Select Profile", range(len(profile_names)), format_func=lambda x: profile_names[x])
            
            current_profile = profiles_list[selected_idx]
            
            col_edit1, col_edit2 = st.columns([3, 1])
            with col_edit1:
                new_name = st.text_input("Profile Name", value=current_profile.get('name', ''))
            
            new_content = st.text_area("Edit Team Profile", value=current_profile.get('content', ''), height=300)
            
            btn_col1, btn_col2, btn_col3 = st.columns(3)
            if btn_col1.button("Save Changes"):
                save_profile(team_num, selected_idx, new_name, new_content)
                st.success("Profile updated!")
                st.rerun()
            
            if btn_col2.button("Add New Profile"):
                save_profile(team_num, None, "New Profile", "")
                st.rerun()
                
            if btn_col3.button("Delete This Profile"):
                delete_profile(team_num, selected_idx)
                st.warning("Profile deleted.")
                st.rerun()
            
            st.divider()
            st.markdown(f"### Profile: {current_profile.get('name')}")
            if current_profile.get('content'):
                st.markdown(current_profile.get('content'))
            else:
                st.write("*No content in this profile yet.*")
            
        # File Upload Option
        st.divider()
        st.subheader("Upload Profile Document")
        uploaded_file = st.file_uploader("Upload a document", type=['txt', 'md'])
        if uploaded_file:
            # Check if the file has already been read in this session to avoid errors
            try:
                # Use string conversion to ensure we get text, or decode if bytes
                raw_data = uploaded_file.getvalue()
                try:
                    content = raw_data.decode("utf-8")
                except UnicodeDecodeError:
                    # Fallback to Latin-1 if UTF-8 fails (common for some Windows-encoded files)
                    content = raw_data.decode("latin-1")
                
                upload_name = st.text_input("Uploaded Profile Name", value=uploaded_file.name)
                if st.button("Add Uploaded Content as New Profile"):
                    save_profile(team_num, None, upload_name, content)
                    st.success("Uploaded profile added!")
                    st.rerun()
            except Exception as e:
                st.error(f"Error reading file: {e}")

else:
    st.error(f"Team {team_num} not found. Please check the team number.")

# Footer
st.divider()
st.caption("Data provided by FTCScout.org REST API")
