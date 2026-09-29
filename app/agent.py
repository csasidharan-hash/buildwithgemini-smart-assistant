import datetime
import json
import math
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types
from google import genai
from google.cloud import firestore, storage

from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
from app.a2ui_utils import a2ui_callback



# Load local environment variables from .env
load_dotenv()

# Hardcoded project ID string for Firestore client (do NOT use GOOGLE_CLOUD_PROJECT env var)
FIRESTORE_PROJECT = "qwiklabs-gcp-02-4755a5ae1485"
# Hardcoded public Cloud Storage bucket name string
STORAGE_BUCKET = "smart-assistant-assets-qwiklabs-gcp-02-4755a5ae1485"

# Load Agent Engine resource name from deployment_metadata.json if present
AGENT_ENGINE_RESOURCE_NAME = None
_meta_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(_meta_path):
    try:
        with open(_meta_path, "r") as _f:
            _meta = json.load(_f)
            AGENT_ENGINE_RESOURCE_NAME = _meta.get("remote_agent_runtime_id")
    except Exception:
        pass




def get_firestore_client():
    return firestore.Client(project=FIRESTORE_PROJECT)


def geocode_address(address: str) -> str:
    """Convert a street address or place name into latitude and longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The street address or place name (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A formatted string with address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not configured in environment."

    try:
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "SmartAssistant/1.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") == "OK" and data.get("results"):
            res = data["results"][0]
            loc = res["geometry"]["location"]
            fmt_addr = res.get("formatted_address", address)
            lat, lng = loc.get("lat"), loc.get("lng")
            return f"Geocoded address '{fmt_addr}': Latitude {lat}, Longitude {lng}"
        return f"Geocoding failed with status '{data.get('status')}': {data.get('error_message', 'No results found.')}"
    except Exception as e:
        return f"Geocoding exception: {str(e)}"


def find_nearby_places(latitude: float, longitude: float, place_type: str = "restaurant", radius_meters: float = 1000.0) -> str:
    """Find nearby points of interest (e.g. restaurants, cafes, parks) using Google Places API (New).

    Args:
        latitude: Latitude of search center.
        longitude: Longitude of search center.
        place_type: Type of place (e.g., 'restaurant', 'cafe', 'park', 'museum', 'tourist_attraction').
        radius_meters: Search radius in meters (default 1000m).

    Returns:
        A formatted string listing nearby places containing name, address, and location (latitude/longitude).
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "Error: GOOGLE_MAPS_API_KEY is not configured in environment."

    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
            "User-Agent": "SmartAssistant/1.0"
        }
        body = {
            "includedTypes": [place_type],
            "maxResultCount": 5,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius_meters)
                }
            }
        }
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        results = []
        for place in data.get("places", []):
            name = place.get("displayName", {}).get("text", "Unknown")
            addr = place.get("formattedAddress", "N/A")
            loc = place.get("location", {})
            plat, plng = loc.get("latitude"), loc.get("longitude")
            results.append(f"- Name: {name}, Address: {addr}, Location: ({plat}, {plng})")
        if not results:
            return f"No nearby {place_type} places found within {radius_meters}m."
        return "\n".join(results)
    except Exception as e:
        return f"Places search exception: {str(e)}"



def fetch_live_weather(location: str) -> str:
    """Fetch real-time live weather conditions for any city globally using Open-Meteo.

    Args:
        location: City or place name (e.g., 'New York', 'Paris', 'Tokyo', 'Sydney').

    Returns:
        A string summary of current live temperature (°F), weather conditions, humidity, and wind speed.
    """
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "SmartAssistant/1.0"})
        with urllib.request.urlopen(req) as resp:
            geo_data = json.loads(resp.read().decode())

        if not geo_data.get("results"):
            return f"Could not find coordinates for location: {location}"

        place = geo_data["results"][0]
        name = place.get("name", location)
        country = place.get("country", "")
        lat, lon = place["latitude"], place["longitude"]

        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&temperature_unit=fahrenheit"
        )
        req2 = urllib.request.Request(weather_url, headers={"User-Agent": "SmartAssistant/1.0"})
        with urllib.request.urlopen(req2) as resp2:
            w_data = json.loads(resp2.read().decode())

        current = w_data.get("current", {})
        temp = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind = current.get("wind_speed_10m", "N/A")
        code = current.get("weather_code", -1)

        code_map = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            95: "Thunderstorm"
        }
        condition = code_map.get(code, "Cloudy")

        loc_label = f"{name}, {country}" if country else name
        return f"Live weather for {loc_label}: {temp}°F, {condition}. Humidity: {humidity}%, Wind: {wind} mph."
    except Exception as e:
        return f"Error fetching live weather for {location}: {str(e)}"


def get_air_quality(location: str) -> str:
    """Fetch real-time Air Quality Index (AQI) and pollutant levels for a city.

    Args:
        location: City or location name (e.g., 'New York', 'Beijing', 'London', 'Paris').

    Returns:
        Summary of US AQI, PM2.5, and PM10 particulate levels.
    """
    try:
        api_key = os.environ.get("AIR_QUALITY_API_KEY", "")

        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "SmartAssistant/1.0"})
        with urllib.request.urlopen(req) as resp:
            geo_data = json.loads(resp.read().decode())

        if not geo_data.get("results"):
            return f"Could not find coordinates for location: {location}"

        place = geo_data["results"][0]
        name = place.get("name", location)
        country = place.get("country", "")
        lat, lon = place["latitude"], place["longitude"]

        aqi_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi,pm10,pm2_5"
        if api_key:
            aqi_url += f"&apikey={api_key}"

        req2 = urllib.request.Request(aqi_url, headers={"User-Agent": "SmartAssistant/1.0"})
        with urllib.request.urlopen(req2) as resp2:
            aqi_data = json.loads(resp2.read().decode())

        current = aqi_data.get("current", {})
        us_aqi = current.get("us_aqi", "N/A")
        pm2_5 = current.get("pm2_5", "N/A")
        pm10 = current.get("pm10", "N/A")

        if isinstance(us_aqi, (int, float)):
            if us_aqi <= 50:
                qual = "Good"
            elif us_aqi <= 100:
                qual = "Moderate"
            elif us_aqi <= 150:
                qual = "Unhealthy for Sensitive Groups"
            elif us_aqi <= 200:
                qual = "Unhealthy"
            else:
                qual = "Very Unhealthy"
        else:
            qual = "Unknown"

        loc_label = f"{name}, {country}" if country else name
        return f"Air quality for {loc_label}: US AQI {us_aqi} ({qual}). PM2.5: {pm2_5} μg/m³, PM10: {pm10} μg/m³."
    except Exception as e:
        return f"Error fetching air quality for {location}: {str(e)}"


def list_saved_locations(category: str | None = None) -> list[dict]:
    """List saved locations from the Firestore catalog.

    Args:
        category: Optional category filter (e.g., 'Metropolis', 'Tech Hub', 'European Capital').

    Returns:
        A list of location records stored in Firestore.
    """
    db = get_firestore_client()
    docs_ref = db.collection("saved_locations")
    if category:
        query = docs_ref.where("category", "==", category)
        docs = query.stream()
    else:
        docs = docs_ref.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


def save_location(
    location_id: str,
    city: str,
    country: str,
    category: str,
    notes: str,
    temperature_f: int,
    condition: str
) -> str:
    """Save or update a location in the Firestore catalog.

    Args:
        location_id: Unique identifier string (e.g., 'chicago', 'berlin').
        city: City name.
        country: Country name.
        category: Location category.
        notes: Relevant notes or description.
        temperature_f: Temperature in Fahrenheit.
        condition: Weather condition string.

    Returns:
        A confirmation string indicating the location was saved.
    """
    db = get_firestore_client()
    doc_ref = db.collection("saved_locations").document(location_id.lower())
    record = {
        "id": location_id.lower(),
        "city": city,
        "country": country,
        "category": category,
        "notes": notes,
        "temperature_f": temperature_f,
        "condition": condition,
    }
    doc_ref.set(record)
    return f"Successfully saved location '{city}, {country}' to Firestore catalog."


def get_weather(location: str) -> str:
    """Get the current weather conditions for a given city or location.

    Args:
        location: The name of the city or location (e.g., 'New York', 'San Francisco', 'Tokyo').

    Returns:
        A summary of the current weather conditions and temperature.
    """
    return fetch_live_weather(location)


def get_current_time(city: str) -> str:
    """Get the current local time for a specific city.

    Args:
        city: The name of the city (e.g., 'San Francisco', 'New York', 'London', 'Tokyo').

    Returns:
        A string containing the current local time and time zone.
    """
    city_lower = city.lower()
    if "sf" in city_lower or "san francisco" in city_lower:
        tz_identifier = "America/Los_Angeles"
    elif "ny" in city_lower or "new york" in city_lower:
        tz_identifier = "America/New_York"
    elif "london" in city_lower:
        tz_identifier = "Europe/London"
    elif "tokyo" in city_lower:
        tz_identifier = "Asia/Tokyo"
    else:
        tz_identifier = "UTC"

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time in {city.title()} is {now.strftime('%I:%M %p %Z (%Y-%m-%d)')}."


def calculate(expression: str) -> str:
    """Evaluate a mathematical calculation expression safely.

    Args:
        expression: A mathematical expression to calculate, e.g., '15 * 12 + 45' or 'sqrt(144)'.

    Returns:
        The evaluated result as a string.
    """
    try:
        allowed_names = {
            "abs": abs, "round": round, "min": min, "max": max,
            "pow": pow, "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
            "sin": math.sin, "cos": math.cos, "tan": math.tan
        }
        result = eval(expression, {"__builtins__": None}, allowed_names)
        return f"Calculation result: {result}"
    except Exception as e:
        return f"Error evaluating expression '{expression}': {str(e)}"


def generate_location_image(prompt: str, tool_context: ToolContext) -> str:
    """Generate an image for a location, landscape, or weather scene using Gemini Image Model.

    Args:
        prompt: Detailed description of the image to generate (e.g. 'A scenic view of Eiffel Tower in Paris under sunset with clear skies').
        tool_context: ADK ToolContext automatically injected by the framework.

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage.
    """
    try:
        # Initialize Google GenAI client with global location on Vertex AI
        genai_client = genai.Client(
            vertexai=True,
            location="global",
            project=FIRESTORE_PROJECT
        )

        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"Generate an image of: {prompt}"
        )

        image_bytes = None
        mime_type = "image/png"

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not image_bytes:
            return "Error: No image content returned by gemini-3.1-flash-lite-image model."

        # Create unique timestamped filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "png" if "png" in mime_type.lower() else "jpg"
        filename = f"generated_location_{timestamp}.{ext}"

        # 1. Save artifact with tool_context so it shows up in Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        if tool_context:
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes directly to public Cloud Storage bucket without writing to local file
        storage_client = storage.Client(project=FIRESTORE_PROJECT)
        bucket = storage_client.bucket(STORAGE_BUCKET)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{STORAGE_BUCKET}/{filename}"
        return public_url

    except Exception as e:
        return f"Error generating location image: {str(e)}"


async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


def memory_bank_service_builder():
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT,
        location="us-central1",
        agent_engine_id="272511208165212160",
    )


code_executor = None
if AGENT_ENGINE_RESOURCE_NAME:
    code_executor = AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME
    )

# A2UI Schema Manager (v0.8 with BasicCatalog)
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a friendly and efficient smart assistant. "
        "You remember the user's stated preferences, facts, and ALLERGIES (such as food, drug, or environmental allergies) "
        "from previous conversations and use them to personalize your responses, provide warnings, and ensure user safety."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
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

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=code_executor,
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        geocode_address,
        find_nearby_places,
        fetch_live_weather,
        get_air_quality,
        generate_location_image,
        get_weather,
        get_current_time,
        calculate,
        list_saved_locations,
        save_location,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)


app = App(
    root_agent=root_agent,
    name="app",
)








