from sqlalchemy.orm import Session

from app.models.document import Document
from app.services.google_auth import build_service, credentials_for_user, has_scope

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def sync_drive_documents(db: Session, user_id: int) -> int:
    creds = credentials_for_user(db, user_id)
    if not creds or not has_scope(creds, DRIVE_SCOPE):
        return 0
    service = build_service("drive", "v3", creds)
    response = service.files().list(
        q="trashed = false",
        pageSize=1000,
        fields="files(id,name,mimeType,size,modifiedTime,webViewLink)",
        orderBy="modifiedTime desc",
    ).execute()
    synced = 0
    for file in response.get("files", []):
        link = file.get("webViewLink") or f"drive://{file['id']}"
        row = db.query(Document).filter(Document.user_id == user_id, Document.storage_path == link).first()
        if row is None:
            db.add(Document(
                filename=file.get("name") or "Google Drive file",
                file_type=file.get("mimeType") or "drive",
                file_size=int(file.get("size") or 0),
                storage_path=link,
                raw_text=f"Google Drive file: {file.get('name')}\nType: {file.get('mimeType')}\nLink: {link}",
                user_id=user_id,
            ))
            synced += 1
    db.commit()
    return synced
