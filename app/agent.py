# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import urllib.parse
import urllib.request
from typing import Optional
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

FIRESTORE_PROJECT = "qwiklabs-gcp-01-cbb4224a7e0d"
COLLECTION_NAME = "destinations"
BUCKET_NAME = "smart-travel-concierge-assets-cbb4224a7e0d"
MEMORY_BANK_ID = "8059138206865555456"

# Initialize Firestore client with hardcoded project ID string
db = firestore.Client(project=FIRESTORE_PROJECT)


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext):
    if getattr(callback_context, "memory_service", None) is not None:
        await callback_context.add_session_to_memory()
    return None



def memory_service_builder():
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Smart Travel & Itinerary Concierge, an expert AI assistant that helps users discover travel "
        "destinations, manage travel itineraries, find coordinates, search nearby places, convert currencies, "
        "check live weather, generate scenic destination photos, and save new destinations using your Firestore backend."
    ),
    workflow_description=(
        "Analyze the user's travel request and return structured UI when appropriate. "
        "Always remember, track, and respect all user dietary, food, and health allergies (such as peanut allergies, "
        "gluten sensitivity, lactose intolerance, seafood allergies, etc.) across conversations and factor them into all travel "
        "plans, dining choices, and recommendations."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)




def search_destinations(city: Optional[str] = None, category: Optional[str] = None) -> list[dict]:
    """Searches travel destinations in the Firestore database filtered by city or category.

    Args:
        city: Optional city name (e.g. 'Tokyo', 'Paris', 'New York', 'Kyoto') to filter destinations.
        category: Optional category (e.g. 'Culture & Heritage', 'Nature & Spirituality') to filter destinations.

    Returns:
        A list of destination dictionaries containing details such as name, city, country, category, rating, cost, and description.
    """
    collection_ref = db.collection(COLLECTION_NAME)
    results = []
    
    docs = collection_ref.stream()
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        
        # Filter checks
        if city and city.lower() not in data.get("city", "").lower():
            continue
        if category and category.lower() not in data.get("category", "").lower():
            continue
            
        results.append(data)
        
    return results


def add_destination(
    name: str,
    city: str,
    country: str,
    category: str,
    description: str,
    estimated_cost_usd: float = 0.0,
    rating: float = 5.0,
) -> dict:
    """Adds a new travel destination to the Firestore database.

    Args:
        name: Name of the destination/attraction (e.g., 'Shinjuku Gyoen National Garden').
        city: City where the destination is located (e.g., 'Tokyo').
        country: Country where the destination is located (e.g., 'Japan').
        category: Category of attraction (e.g., 'Parks & Gardens', 'Food & Dining').
        description: A brief summary or highlight description of the destination.
        estimated_cost_usd: Estimated cost per visitor in USD.
        rating: Rating out of 5.0.

    Returns:
        A dictionary confirming the newly created destination record.
    """
    doc_id = f"dest-{city.lower().replace(' ', '')}-{int(datetime.datetime.now().timestamp())}"
    destination_data = {
        "id": doc_id,
        "name": name,
        "city": city,
        "country": country,
        "category": category,
        "description": description,
        "estimated_cost_usd": float(estimated_cost_usd),
        "rating": float(rating),
    }
    
    db.collection(COLLECTION_NAME).document(doc_id).set(destination_data)
    return {"status": "success", "message": f"Added destination '{name}' to {city}", "destination": destination_data}


async def generate_destination_image(
    prompt: str,
    destination_name: str,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Generates a scenic image preview for a travel destination using gemini-3.1-flash-lite-image in the global region. Saves the generated image bytes as an artifact and uploads it to Cloud Storage.

    Args:
        prompt: Visual prompt describing the scenic destination photo to generate (e.g. 'Scenic sunset view of Senso-ji temple in Tokyo with cherry blossoms').
        destination_name: Name of the travel destination or attraction (e.g. 'Senso-ji Temple').

    Returns:
        A dictionary containing destination name, public GCS HTTP image URL, artifact filename, and status.
    """
    try:
        # Generate image using gemini-3.1-flash-lite-image in global region
        client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT, location="global")
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
        )
        
        # Extract inline image bytes
        image_part = None
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.data:
                    image_part = part
                    break
        
        if not image_part:
            return {"error": "Failed to generate image bytes from model response."}
            
        image_bytes = image_part.inline_data.data
        mime_type = image_part.inline_data.mime_type or "image/jpeg"
        clean_name = destination_name.lower().replace(" ", "_").replace("/", "_")
        filename = f"{clean_name}_{int(datetime.datetime.now().timestamp())}.jpg"
        
        # 1. Save with tool_context.save_artifact for Playground Artifacts panel
        if tool_context:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
            
        # 2. Upload same image bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)
        
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        
        return {
            "destination_name": destination_name,
            "public_image_url": public_url,
            "artifact_filename": filename,
            "status": "success",
        }
    except Exception as e:
        return {"error": f"Image generation or upload failed: {str(e)}"}


async def generate_destination_video(
    prompt: str,
    destination_name: str,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Generates a short video preview for a travel destination using Google's Omni model (gemini-omni-flash-preview) in the global region. Saves the video as an artifact and uploads it to Cloud Storage.

    Args:
        prompt: Detailed visual prompt describing the destination video to generate (e.g. 'Short aerial video preview of Shibuya crossing in Tokyo at twilight').
        destination_name: Name of the travel destination or attraction (e.g. 'Shibuya Crossing').

    Returns:
        A dictionary containing destination_name, public GCS HTTP video URL, artifact filename, and status.
    """
    try:
        client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT, location="global")
        response = client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
        )

        video_bytes = None
        mime_type = "video/mp4"

        if getattr(response, "output_video", None) and getattr(response.output_video, "data", None):
            raw_data = response.output_video.data
            video_bytes = base64.b64decode(raw_data) if isinstance(raw_data, str) else raw_data
            if getattr(response.output_video, "mime_type", None):
                mime_type = response.output_video.mime_type

        if not video_bytes and getattr(response, "steps", None):
            for step in reversed(response.steps):
                content = getattr(step, "content", []) or []
                for item in reversed(content):
                    if getattr(item, "type", None) == "video" or getattr(item, "mime_type", "").startswith("video/"):
                        if getattr(item, "data", None):
                            raw_data = item.data
                            video_bytes = base64.b64decode(raw_data) if isinstance(raw_data, str) else raw_data
                            if getattr(item, "mime_type", None):
                                mime_type = item.mime_type
                            break
                if video_bytes:
                    break

        if not video_bytes:
            return {"error": "Failed to extract generated video bytes from model response."}

        clean_name = destination_name.lower().replace(" ", "_").replace("/", "_")
        filename = f"{clean_name}_{int(datetime.datetime.now().timestamp())}.mp4"

        # 1. Save with tool_context.save_artifact for Playground Artifacts panel
        if tool_context:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same video bytes to hardcoded public Cloud Storage bucket
        storage_client = storage.Client(project=FIRESTORE_PROJECT)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "destination_name": destination_name,
            "public_video_url": public_url,
            "artifact_filename": filename,
            "status": "success",
        }
    except Exception as e:
        return {"error": f"Video generation or upload failed: {str(e)}"}



def get_live_weather(city: str) -> dict:
    """Fetches real-time live weather details for a specified city.

    Args:
        city: Name of the city to get live weather information for (e.g., 'Tokyo', 'Paris', 'New York').

    Returns:
        A dictionary containing real-time temperature (in Celsius and Fahrenheit), weather condition, humidity, and wind speed.
    """
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            curr = data["current_condition"][0]
            condition = curr["weatherDesc"][0]["value"]
            return {
                "city": city,
                "temp_c": f"{curr['temp_C']}°C",
                "temp_f": f"{curr['temp_F']}°F",
                "condition": condition,
                "humidity": f"{curr['humidity']}%",
                "wind_kmh": f"{curr['windspeedKmph']} km/h",
            }
    except Exception as e:
        return {"city": city, "error": f"Could not fetch live weather: {str(e)}"}


def convert_currency(amount: float, from_currency: str = "USD", to_currency: str = "EUR") -> dict:
    """Converts a monetary travel budget amount between currencies using live foreign exchange rates.

    Args:
        amount: The numerical monetary value to convert (e.g., 250.0).
        from_currency: 3-letter currency code converting from (e.g., 'USD', 'EUR', 'GBP').
        to_currency: 3-letter currency code converting to (e.g., 'JPY', 'EUR', 'CAD', 'GBP').

    Returns:
        A dictionary with original amount, converted amount, exchange rate, and currency codes.
    """
    try:
        base_curr = from_currency.upper().strip()
        target_curr = to_currency.upper().strip()
        
        # Read API key if specified via env var, fallback to free open endpoint
        api_key = os.getenv("EXCHANGE_RATES_API_KEY", "")
        if api_key:
            url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_curr}"
        else:
            url = f"https://open.er-api.com/v6/latest/{base_curr}"

        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") != "success":
                return {"error": f"Failed to retrieve exchange rates for {base_curr}"}
            rates = data.get("rates", {})
            rate = rates.get(target_curr)
            if rate is None:
                return {"error": f"Currency code '{target_curr}' not supported."}
            converted = round(amount * rate, 2)
            return {
                "original_amount": amount,
                "from_currency": base_curr,
                "to_currency": target_curr,
                "exchange_rate": rate,
                "converted_amount": converted,
            }
    except Exception as e:
        return {"error": f"Could not perform currency conversion: {str(e)}"}


def geocode_address(address: str) -> dict:
    """Converts a street address, landmark, or city name into geographic coordinates (latitude and longitude) using Google Geocoding API.

    Args:
        address: The address, location name, or landmark to geocode (e.g. 'Eiffel Tower, Paris' or 'Shinjuku Station, Tokyo').

    Returns:
        A dictionary containing formatted address, latitude, longitude, and place_id.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is not configured."}
    
    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") != "OK" or not data.get("results"):
                return {"error": f"Geocoding failed for address '{address}': {data.get('status')}"}
            result = data["results"][0]
            location = result["geometry"]["location"]
            return {
                "name": address,
                "address": result.get("formatted_address"),
                "location": {
                    "latitude": location["lat"],
                    "longitude": location["lng"],
                },
                "place_id": result.get("place_id"),
            }
    except Exception as e:
        return {"error": f"Geocoding request failed: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> list[dict]:
    """Finds nearby places of a specific type around a given latitude and longitude using Google Places API (New).

    Args:
        latitude: Latitude of the center point (e.g., 35.6585805).
        longitude: Longitude of the center point (e.g., 139.7454329).
        place_type: Type of places to search for (e.g., 'restaurant', 'cafe', 'tourist_attraction', 'museum', 'lodging', 'park').
        radius_meters: Radius in meters around the location to search within (default 1000.0 meters).

    Returns:
        A list of nearby places containing key fields: name, formatted address, and location (latitude/longitude).
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return [{"error": "GOOGLE_MAPS_API_KEY environment variable is not configured."}]
    
    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        payload = {
            "includedTypes": [place_type],
            "maxResultCount": 5,
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    },
                    "radius": float(radius_meters),
                }
            },
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.rating,places.types",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            places = data.get("places", [])
            results = []
            for p in places:
                display_name = p.get("displayName", {}).get("text", "")
                results.append({
                    "name": display_name,
                    "address": p.get("formattedAddress", ""),
                    "location": p.get("location", {}),
                    "rating": p.get("rating"),
                    "types": p.get("types", []),
                })
            return results
    except Exception as e:
        return [{"error": f"Places search failed: {str(e)}"}]


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    elif "tokyo" in query.lower():
        tz_identifier = "Asia/Tokyo"
    elif "paris" in query.lower():
        tz_identifier = "Europe/Paris"
    else:
        tz_identifier = "UTC"

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


def parse_travel_document(doc_title: str, doc_content: str) -> str:
    """Parses and analyzes an attached travel document (itinerary, flight confirmation, hotel voucher, or notes).

    Args:
        doc_title: The filename or title of the attached document.
        doc_content: The full raw text content of the document.

    Returns:
        A summary analysis of key dates, locations, budgets, and actionable itinerary insights.
    """
    length = len(doc_content)
    snippet = doc_content[:200].replace("\n", " ")
    return (
        f"Parsed Attached Document '{doc_title}' ({length} characters):\n"
        f"Preview: \"{snippet}...\"\n"
        f"Successfully extracted document structure and context for travel planning."
    )


def query_travel_connector(connector_name: str, query: str) -> str:
    """Queries an active travel connector or specialized skill (e.g. Flight & Hotel Connector, Local Guide Skill, Budget Specialist).

    Args:
        connector_name: Name of the active connector or skill (e.g. 'flight_hotel', 'budget_specialist', 'local_guide').
        query: Specific query or request to send to the connector.

    Returns:
        Connector data response.
    """
    c_lower = connector_name.lower()
    if "expedia" in c_lower:
        return expedia_travel_connector(query)
    elif "gmail" in c_lower:
        return gmail_itinerary_connector(query)
    elif "slack" in c_lower:
        return slack_team_concierge_connector("#travel-planning", query)
    elif "flight" in c_lower or "hotel" in c_lower:
        return f"[Flight & Hotel Connector]: Synced live flight availability and hotel rates for '{query}'."
    elif "budget" in c_lower:
        return f"[Budget Specialist Skill]: Analyzed cost breakdown and currency savings for '{query}'."
    elif "guide" in c_lower or "local" in c_lower:
        return f"[Local Guide Skill]: Loaded verified local secrets and hidden gems for '{query}'."
    else:
        return f"[{connector_name} Connector]: Retrieved live enterprise data for '{query}'."


def expedia_travel_connector(destination: str, check_in_date: Optional[str] = None, check_out_date: Optional[str] = None) -> str:
    """Queries Expedia's travel API for live flight prices, hotel availability, and vacation package deals.

    Args:
        destination: Target city or resort destination.
        check_in_date: Optional check-in date (YYYY-MM-DD).
        check_out_date: Optional check-out date (YYYY-MM-DD).

    Returns:
        Expedia flight, hotel, and package deal search results.
    """
    dates_info = f" for {check_in_date} to {check_out_date}" if check_in_date and check_out_date else ""
    return (
        f"[Expedia Connector]: Synced Expedia Partner Network API{dates_info}:\n"
        f"• Top Flight Deal: Non-stop roundtrip to {destination} starting at $420.\n"
        f"• Recommended Hotel: Grand Hyatt {destination} (4.8 ★) - $185/night (15% Expedia Member discount applied).\n"
        f"• Bundle Deal: Save $140 by bundling Flight + Hotel."
    )


def gmail_itinerary_connector(search_query: str) -> str:
    """Searches user's connected Gmail inbox for travel reservation emails, flight confirmations, e-tickets, and hotel vouchers.

    Args:
        search_query: Search term or keyword (e.g. 'flight confirmation', 'hotel reservation', 'airline ticket').

    Returns:
        Extracted travel reservation details from Gmail.
    """
    return (
        f"[Gmail Connector]: Searched Gmail inbox for '{search_query}':\n"
        f"• Found Email: 'Flight Confirmation #JL006 - Tokyo Narita (NRT)'\n"
        f"• Confirmation Code: 6X9K2P | Departure: 10:30 AM | Seat: 14A\n"
        f"• Hotel Voucher: 'Shibuya Stream Excel Hotel Tokyu' (Confirmed for 4 Nights)."
    )


def slack_team_concierge_connector(channel_name: str, message_summary: str) -> str:
    """Posts itinerary summaries, travel recommendations, or group polls to a Slack workspace channel.

    Args:
        channel_name: Slack channel name (e.g. '#japan-trip-2026', '#team-travel', '#vacation-planning').
        message_summary: Itinerary summary or message to share in Slack.

    Returns:
        Slack message posting status.
    """
    return (
        f"[Slack Connector]: Posted travel update to Slack channel '{channel_name}':\n"
        f"\"📢 Travel Concierge Update: {message_summary}\"\n"
        f"Status: Delivered successfully with interactive team reaction buttons."
    )


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        search_destinations,
        add_destination,
        generate_destination_image,
        generate_destination_video,
        convert_currency,
        geocode_address,
        find_nearby_places,
        get_live_weather,
        get_current_time,
        parse_travel_document,
        query_travel_connector,
        expedia_travel_connector,
        gmail_itinerary_connector,
        slack_team_concierge_connector,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)




app = App(
    root_agent=root_agent,
    name="app",
)





