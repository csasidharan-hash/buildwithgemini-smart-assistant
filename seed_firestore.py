"""Seed script for Firestore database."""
from google.cloud import firestore

# CRITICAL: Hardcoded project ID string for Firestore client
FIRESTORE_PROJECT = "qwiklabs-gcp-02-4755a5ae1485"

SEEDED_LOCATIONS = [
    {
        "id": "new_york",
        "city": "New York",
        "country": "USA",
        "category": "Metropolis",
        "notes": "East coast financial and cultural hub.",
        "temperature_f": 72,
        "condition": "Clear and Sunny"
    },
    {
        "id": "san_francisco",
        "city": "San Francisco",
        "country": "USA",
        "category": "Tech Hub",
        "notes": "West coast tech center with foggy mornings.",
        "temperature_f": 62,
        "condition": "Partly Cloudy"
    },
    {
        "id": "tokyo",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Metropolis",
        "notes": "Vibrant capital with rich culture and dining.",
        "temperature_f": 68,
        "condition": "Light Rain"
    },
    {
        "id": "london",
        "city": "London",
        "country": "UK",
        "category": "European Capital",
        "notes": "Historic capital along the River Thames.",
        "temperature_f": 58,
        "condition": "Overcast"
    },
    {
        "id": "paris",
        "city": "Paris",
        "country": "France",
        "category": "European Capital",
        "notes": "City of lights, famous for art and fashion.",
        "temperature_f": 65,
        "condition": "Sunny"
    }
]

def seed_database():
    db = firestore.Client(project=FIRESTORE_PROJECT)
    collection_ref = db.collection("saved_locations")
    
    print(f"Seeding 'saved_locations' collection in Firestore project '{FIRESTORE_PROJECT}'...")
    for item in SEEDED_LOCATIONS:
        doc_id = item["id"]
        doc_ref = collection_ref.document(doc_id)
        doc_ref.set(item)
        print(f"  ✓ Seeded document: {doc_id} -> {item['city']}, {item['country']}")
    
    print("Firestore seeding complete!")

if __name__ == "__main__":
    seed_database()
