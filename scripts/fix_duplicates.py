"""Script to fix duplicate meetings in the database."""
from app import db
from app.models.tables import Reuniao
from app.services.google_calendar import list_upcoming_events
from flask import current_app

def fix_meetings():
    """Find and fix any duplicate meetings in the database."""
    # Get all meetings from the database
    meetings = Reuniao.query.all()
    
    # Group meetings by google_event_id
    meeting_dict = {}
    for meeting in meetings:
        if meeting.google_event_id:
            if meeting.google_event_id not in meeting_dict:
                meeting_dict[meeting.google_event_id] = []
            meeting_dict[meeting.google_event_id].append(meeting)
    
    # Find duplicates
    duplicates = {
        event_id: meeting_list 
        for event_id, meeting_list in meeting_dict.items() 
        if len(meeting_list) > 1
    }
    
    if not duplicates:
        print("No duplicate meetings found!")
        return
    
    print(f"Found {len(duplicates)} events with duplicate meetings:")
    
    # Get events from Google Calendar
    try:
        calendar_events = {
            event["id"]: event 
            for event in list_upcoming_events()
        }
    except Exception as e:
        print(f"Error getting calendar events: {e}")
        calendar_events = {}
    
    # Fix duplicates
    for event_id, duplicate_meetings in duplicates.items():
        print(f"\nEvent ID: {event_id}")
        print(f"Found {len(duplicate_meetings)} duplicate meetings:")
        
        # Sort meetings by start time, newest first
        duplicate_meetings.sort(key=lambda m: m.inicio, reverse=True)
        
        # Check if event still exists in Google Calendar
        calendar_event = calendar_events.get(event_id)
        if calendar_event:
            print(f"Event exists in Google Calendar: {calendar_event.get('summary')}")
            
            # Keep most recent meeting
            keep_meeting = duplicate_meetings[0]
            print(f"Keeping meeting ID {keep_meeting.id} (newest)")
            
            # Delete other duplicates
            for meeting in duplicate_meetings[1:]:
                print(f"Deleting duplicate meeting ID {meeting.id}")
                db.session.delete(meeting)
        else:
            print("Event not found in Google Calendar")
            # If event doesn't exist in Google Calendar, delete all duplicates
            for meeting in duplicate_meetings:
                print(f"Deleting orphaned meeting ID {meeting.id}")
                db.session.delete(meeting)
    
    # Commit changes
    try:
        db.session.commit()
        print("\nSuccessfully fixed duplicate meetings!")
    except Exception as e:
        print(f"\nError fixing duplicates: {e}")
        db.session.rollback()

if __name__ == '__main__':
    fix_meetings()