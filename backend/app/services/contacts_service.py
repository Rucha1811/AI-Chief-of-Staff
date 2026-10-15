import json

from sqlalchemy.orm import Session

from app.models.contact import Contact
from app.services.google_auth import build_service, credentials_for_user, has_scope

CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"


def sync_contacts(db: Session, user_id: int) -> int:
    creds = credentials_for_user(db, user_id)
    if not creds or not has_scope(creds, CONTACTS_SCOPE):
        return 0
    service = build_service("people", "v1", creds)
    synced = 0
    token = None
    while True:
        response = service.people().connections().list(
            resourceName="people/me",
            pageSize=1000,
            pageToken=token,
            personFields="names,emailAddresses,phoneNumbers",
        ).execute()
        for person in response.get("connections", []):
            names = person.get("names") or []
            name = next((item.get("displayName") for item in names if item.get("displayName")), None)
            if not name:
                continue
            emails = [item.get("value") for item in person.get("emailAddresses", []) if item.get("value")]
            phones = [item.get("value") for item in person.get("phoneNumbers", []) if item.get("value")]
            row = db.query(Contact).filter(Contact.user_id == user_id, Contact.resource_name == person["resourceName"]).first()
            if row is None:
                row = Contact(resource_name=person["resourceName"], user_id=user_id)
                db.add(row)
            row.display_name = name
            row.emails = json.dumps(emails)
            row.phone_numbers = json.dumps(phones)
            synced += 1
        token = response.get("nextPageToken")
        if not token:
            break
    db.commit()
    return synced
