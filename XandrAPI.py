import streamlit as st
import requests
from tenacity import retry, stop_after_attempt, wait_exponential  # Added for retry mechanism
import logging
from concurrent.futures import ThreadPoolExecutor
import time

logging.basicConfig(filename="app.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if st.__version__ < "1.0.0":
    st.error("Please upgrade Streamlit to version 1.0.0 or higher.")

# --- Xandr API Configuration ---
XANDR_BASE_URL = "https://api.appnexus.com"

# --- Helper Functions (Refactored from your script) ---

def make_api_request(method, url, headers=None, params=None, json=None):
    try:
        response = requests.request(method, url, headers=headers, params=params, json=json)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        logging.error(f"API request failed: {e}")
        return None
    except ValueError as e:
        st.error(f"Invalid JSON response: {e}")
        logging.error(f"Invalid JSON response: {e}")
        return None

def get_cities_for_country(token: str, country_name: str) -> list[dict] | None:
    """
    Fetches city IDs for a given country.
    Only active cities are included in the response.
    """
    url = f"{XANDR_BASE_URL}/city"
    headers = {"Authorization": token}
    params = {
        "active": "true"  # Filter for active cities only
    }

    json_response = make_api_request("GET", url, headers=headers, params=params)
    if not json_response:
        return None

    if 'response' not in json_response or 'cities' not in json_response['response']:
        st.error(f"Unexpected API response structure when fetching cities for {country_name}.")
        logging.error(f"Unexpected API response: {json_response}")
        return None

    cities_data = json_response['response']['cities']
    filtered_data = [
        {"id": city['id'], "name": city['name']}
        for city in cities_data
        if city['country_name'].strip().lower() == country_name.strip().lower()
    ]

    if not filtered_data:
        st.warning(f"No active cities found for country: {country_name}.")
        return None

    return filtered_data

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def update_line_item_profile_geo(token: str, profile_id: int, city_targets: list[dict]) -> bool:
    """Updates the geo targeting for a given profile ID."""
    url = f"{XANDR_BASE_URL}/profile?id={profile_id}"
    headers = {"Authorization": token}
    data = {
        "profile": {
            "id": profile_id,
            "city_targets": city_targets,
            "city_action": "include"
        }
    }

    try:
        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        st.error(f"Error updating geo targeting for profile ID {profile_id}: {e}")
        logging.error(f"Error updating geo targeting for profile ID {profile_id}: {e}")
        return False

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def update_conversion_pixel(token: str, advertiser_id: int, line_item_id: int, pixel_id: int) -> bool:
    """
    Updates the conversion pixel for a given line item ID.
    If the pixel already exists, it updates its attributes. Otherwise, it adds the pixel.
    """
    # Step 1: Fetch the existing line item
    url = f"{XANDR_BASE_URL}/line-item?id={line_item_id}&advertiser_id={advertiser_id}"
    headers = {"Authorization": token}

    try:
        # Log the GET request
        logging.info(f"GET Request URL: {url}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        line_item_data = response.json()

        # Log the GET response
        logging.info(f"GET Response for Line Item ID {line_item_id}: {line_item_data}")

        if 'response' not in line_item_data or 'line-item' not in line_item_data['response']:
            st.error(f"Unexpected API response structure for Line Item ID: {line_item_id}.")
            logging.error(f"Unexpected API response: {line_item_data}")
            return False

        # Step 2: Retrieve the existing pixels array
        existing_pixels = line_item_data['response']['line-item'].get('pixels', [])
        logging.info(f"Existing Pixels for Line Item ID {line_item_id}: {existing_pixels}")

        # Step 3: Check if the pixel already exists
        pixel_exists = False
        for pixel in existing_pixels:
            if pixel.get('id') == pixel_id:
                # Update the pixel's state
                pixel['state'] = "active"
                pixel_exists = True
                break

        if not pixel_exists:
            # Add the new pixel to the array
            existing_pixels.append({
                "id": pixel_id,
                "state": "active"
            })

        # Step 4: Send the updated pixels array back to the API
        update_data = {
            "line-item": {
                "id": line_item_id,
                "pixels": existing_pixels
            }
        }

        # Log the PUT request
        logging.info(f"PUT Request URL: {url}")
        logging.info(f"PUT Request Payload: {update_data}")

        update_response = requests.put(url, headers=headers, json=update_data)
        update_response.raise_for_status()

        # Log the PUT response
        logging.info(f"PUT Response for Line Item ID {line_item_id}: {update_response.json()}")
        return True

    except requests.exceptions.RequestException as e:
        st.error(f"Error updating conversion pixel for Line Item ID {line_item_id}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"API Error Response: {e.response.text}")
        logging.error(f"Error updating conversion pixel for Line Item ID {line_item_id}: {e}")
        return False

def get_line_item_ids_from_io(token: str, insertion_order_id: int) -> list[int] | None:
    """Fetches line item IDs for a given insertion order ID."""
    url = f"{XANDR_BASE_URL}/insertion-order?id={insertion_order_id}"
    headers = {"Authorization": token}

    json_response = make_api_request("GET", url, headers=headers)
    if not json_response:
        return None

    if 'response' not in json_response or 'insertion-order' not in json_response['response']:
        st.error(f"Unexpected API response structure for insertion order ID: {insertion_order_id}.")
        logging.error(f"Unexpected API response: {json_response}")
        return None

    line_items = json_response['response']['insertion-order']['line_items']
    return [item['id'] for item in line_items]

def get_profile_id_for_line_item(token: str, line_item_id: int) -> int | None:
    """Fetches the profile ID for a given line item ID."""
    url = f"{XANDR_BASE_URL}/line-item?id={line_item_id}"
    headers = {"Authorization": token}

    json_response = make_api_request("GET", url, headers=headers)
    if not json_response:
        return None

    if 'response' not in json_response or 'line-item' not in json_response['response']:
        st.error(f"Unexpected API response structure for line item ID: {line_item_id}.")
        logging.error(f"Unexpected API response: {json_response}")
        return None

    return json_response['response']['line-item']['profile_id']

def authenticate(username: str, password: str) -> str | None:
    """Authenticates the user and retrieves the API token."""
    credentials = f'{{"auth": {{"username": "{username}", "password": "{password}"}}}}'
    try:
        response = requests.post(f"{XANDR_BASE_URL}/auth", data=credentials)
        response.raise_for_status()
        json_response = response.json()

        if 'response' in json_response and 'token' in json_response['response']:
            return json_response['response']['token']
        else:
            st.error("Authentication failed. Please check your credentials.")
            logging.error(f"Authentication failed: {json_response}")
            return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error during authentication: {e}")
        logging.error(f"Error during authentication: {e}")
        return None

def generate_and_poll_report(advertiser_id_input, use_custom_dates, start_date, end_date, report_interval, report_payload):
    # Add Date Range or Interval
    if use_custom_dates:
        report_payload["report"]["start_date"] = start_date.strftime("%Y-%m-%d")
        report_payload["report"]["end_date"] = end_date.strftime("%Y-%m-%d")
    else:
        report_payload["report"]["report_interval"] = report_interval

    # API Endpoint
    endpoint = f"{XANDR_BASE_URL}/report?advertiser_id={advertiser_id_input.strip()}"

    # Make the API Request to Generate the Report
    json_response = make_api_request("POST", endpoint, headers={"Authorization": st.session_state["api_token"]}, json=report_payload)
    if not json_response:
        st.error("Failed to generate the report. Please check the API response.")
        return

    logging.info(f"API Response: {json_response}")
    st.json(json_response)  # Debugging: Display the API response
    report_id = json_response.get("report_id")
    if not report_id:
        st.error("Failed to retrieve report ID. Please check the API response.")
        return

    # Poll for Report Status
    status_url = f"{XANDR_BASE_URL}/report?id={report_id}"
    max_retries = 10
    retries = 0
    while retries < max_retries:
        try:
            status_response = requests.get(status_url, headers={"Authorization": st.session_state["api_token"]})
            status_response.raise_for_status()
            status_data = status_response.json()
            if status_data.get("execution_status") == "ready":
                break
            elif status_data.get("execution_status") == "error":
                st.error("An error occurred while generating the report.")
                logging.error(f"Report generation error: {status_data}")
                return
            time.sleep(5)
            retries += 1
        except Exception as e:
            st.error(f"An error occurred while checking report status: {e}")
            logging.error(f"Error while polling report status: {e}")
            return
    else:
        st.error("Report generation timed out. Please try again later.")
        return

    # Download the Report
    try:
        download_url = f"{XANDR_BASE_URL}/report-download?id={report_id}"
        report_data = requests.get(download_url, headers={"Authorization": st.session_state["api_token"]})
        report_data.raise_for_status()
        from datetime import datetime

        file_name = f"site_domain_performance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        with open(file_name, "wb") as file:
            file.write(report_data.content)
        st.success("Report downloaded successfully!")
        st.download_button(
            label="Download Report",
            data=report_data.content,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        st.error(f"An error occurred while downloading the report: {e}")
        logging.error(f"Error while downloading the report: {e}")
        return

# --- Streamlit UI ---
st.set_page_config(layout="wide")
st.title("Xandr Tools: Geo Targeting, Conversion Pixels & Reporting")

# Initialize session state variables
if "api_token" not in st.session_state:
    st.session_state["api_token"] = None
if "username" not in st.session_state:
    st.session_state["username"] = None

# --- Login Section ---
st.sidebar.header("Login")

# Check if the user is already logged in
if st.session_state["api_token"]:
    st.sidebar.success(f"Logged in as {st.session_state['username']}")
    logout_button = st.sidebar.button("Log Out")
    if logout_button:
        st.session_state["api_token"] = None
        st.session_state["username"] = None
        st.sidebar.info("You have been logged out.")
else:
    # Show the login form if the user is not logged in
    username = st.sidebar.text_input("Username", placeholder="Enter your username")
    password = st.sidebar.text_input("Password", placeholder="Enter your password", type="password")
    login_button = st.sidebar.button("Log In")

    if login_button:
        if username and password:
            # Authenticate and retrieve the token
            token = authenticate(username, password)
            if token:
                st.session_state["api_token"] = token
                st.session_state["username"] = username
                st.sidebar.success("Logged in successfully!")
            else:
                st.sidebar.error("Login failed. Please check your credentials.")
        else:
            st.sidebar.error("Please enter both username and password.")

# Tabs for different tools
if st.session_state["api_token"]:  # Only show tabs if the user is logged in
    tab1, tab2 = st.tabs(["Geo Targeting Updater", "Conversion Pixel Updater"])  # Exclude the Reporting tab
else:
    tab1, tab2 = st.tabs(["Geo Targeting Updater", "Conversion Pixel Updater"])  # Exclude the Reporting tab

# --- Tab 1: Geo Targeting Updater ---
with tab1:
    st.header("Geo Targeting Updater")
    if st.session_state["api_token"] is None:
        st.error("Please log in to use this tool.")
    else:
        country_name_input = st.text_input("Country Name", placeholder="e.g., Sweden, Germany, United States", key="geo_country_name")
        city_name_input = st.text_input("City Name (Optional)", placeholder="e.g., Stockholm", key="geo_city_name")
        insertion_order_id_input = st.text_input(
            "Insertion Order ID (Optional)", 
            placeholder="Enter a valid Insertion Order ID",
            help="Provide the Insertion Order ID to update all line items within it.",
            key="geo_insertion_order_id"
        )
        line_item_ids_input = st.text_area(
            "Line Item IDs (Optional)", 
            placeholder="Enter line item IDs separated by commas (e.g., 12345, 67890, 11223)",
            help="Provide the line item IDs you want to update. Leave blank to update all line items in the insertion order.",
            key="geo_line_item_ids"
        )
        if st.button("Update Geo Targeting", key="geo_update_button"):
            if not country_name_input.strip():
                st.error("Country Name is required.")
                st.stop()

            # Split city names into a list
            city_names = [city.strip() for city in city_name_input.split(",") if city.strip()]
            if not city_names:
                st.error("Please provide at least one city name.")
                st.stop()

            # Fetch city targets
            city_targets = get_cities_for_country(st.session_state["api_token"], country_name_input)
            if not city_targets:
                st.error("No valid city targets found. Please check your inputs.")
                st.stop()

            # Check which cities were found
            found_cities = []
            not_found_cities = []
            for city in city_names:
                if any(target['name'].lower() == city.lower() for target in city_targets):
                    found_cities.append(city)
                else:
                    not_found_cities.append(city)

            # Notify the user about found and not-found cities
            if found_cities:
                st.success(f"Found cities: {', '.join(found_cities)}")
            if not_found_cities:
                st.warning(f"Cities not found: {', '.join(not_found_cities)}")

            # Determine line items to update
            line_item_ids = []
            if line_item_ids_input.strip():
                # Parse line item IDs from user input
                line_item_ids = [int(item.strip()) for item in line_item_ids_input.split(",") if item.strip().isdigit()]
            elif insertion_order_id_input.strip():
                # Fetch line item IDs from the insertion order
                line_item_ids = get_line_item_ids_from_io(st.session_state["api_token"], int(insertion_order_id_input.strip()))
                if not line_item_ids:
                    st.error("No line items found for the provided Insertion Order ID.")
                    st.stop()
            else:
                st.error("Either Line Item IDs or an Insertion Order ID is required.")
                st.stop()

            # Update geo targeting for each line item
            for line_item_id in line_item_ids:
                profile_id = get_profile_id_for_line_item(st.session_state["api_token"], line_item_id)
                if not profile_id:
                    st.error(f"Profile ID not found for Line Item ID: {line_item_id}")
                    continue

                success = update_line_item_profile_geo(st.session_state["api_token"], profile_id, city_targets)
                if success:
                    st.success(f"Geo targeting updated for Line Item ID: {line_item_id}")
                else:
                    st.error(f"Failed to update geo targeting for Line Item ID: {line_item_id}")

# --- Tab 2: Conversion Pixel Updater ---
with tab2:
    st.header("Conversion Pixel Updater")
    if st.session_state["api_token"] is None:
        st.error("Please log in to use this tool.")
    else:
        insertion_order_id_input = st.text_input(
            "Insertion Order ID (Optional)", 
            placeholder="Enter a valid Insertion Order ID",
            help="Provide the Insertion Order ID to update all line items with the new conversion pixel.",
            key="pixel_insertion_order_id"
        )
        line_item_ids_input = st.text_area(
            "Line Item IDs (Optional)", 
            placeholder="Enter line item IDs separated by commas (e.g., 12345, 67890, 11223)",
            help="Provide the line item IDs you want to update. Leave blank to update all line items in the insertion order.",
            key="pixel_line_item_ids"
        )
        new_pixel_id_input = st.text_input(
            "New Conversion Pixel ID", 
            placeholder="Enter the new conversion pixel ID",
            help="Provide the ID of the new conversion pixel to apply.",
            key="pixel_new_pixel_id"
        )
        advertiser_id_input = st.text_input(
            "Advertiser ID (Required)",
            placeholder="Enter Advertiser ID",
            help="Provide the Advertiser ID associated with the line items.",
            key="pixel_advertiser_id"
        )

        if not advertiser_id_input.strip():
            st.error("Advertiser ID is required.")
            st.stop()

        if not advertiser_id_input.strip().isdigit():
            st.error("Advertiser ID must be a numeric value.")
            st.stop()

        if st.button("Update Conversion Pixels", key="pixel_update_button"):
            # Validate Inputs
            if not new_pixel_id_input.strip():
                st.error("New Conversion Pixel ID is required.")
                st.stop()

            if not new_pixel_id_input.strip().isdigit():
                st.error("Conversion Pixel ID must be a numeric value.")
                st.stop()

            line_item_ids = []
            if line_item_ids_input.strip():
                # Parse line item IDs from user input
                line_item_ids = [int(item.strip()) for item in line_item_ids_input.split(",") if item.strip().isdigit()]
            elif insertion_order_id_input.strip():
                # Validate that the insertion order ID is numeric
                if not insertion_order_id_input.strip().isdigit():
                    st.error("Insertion Order ID must be a numeric value.")
                    st.stop()

                # Fetch line item IDs from the insertion order
                line_item_ids = get_line_item_ids_from_io(st.session_state["api_token"], int(insertion_order_id_input.strip()))
                if not line_item_ids:
                    st.error("No line items found for the provided Insertion Order ID.")
                    st.stop()
            else:
                st.error("Either Line Item IDs or an Insertion Order ID is required.")
                st.stop()

            # Update Conversion Pixel for Each Line Item
            for line_item_id in line_item_ids:
                success = update_conversion_pixel(
                    token=st.session_state["api_token"],
                    advertiser_id=int(advertiser_id_input.strip()),  # Pass advertiser_id from user input
                    line_item_id=line_item_id,
                    pixel_id=int(new_pixel_id_input.strip()),
                )
                if success:
                    st.success(f"Conversion pixel updated for Line Item ID: {line_item_id}")
                else:
                    st.error(f"Failed to update conversion pixel for Line Item ID: {line_item_id}")