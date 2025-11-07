"""Task filtering utilities."""
from sqlalchemy import select
from app.models.tables import Task, TaskFollower

def get_follower_subquery(user_id):
    """Return a subquery for tasks followed by the given user."""
    return select(TaskFollower.task_id).where(
        TaskFollower.user_id == user_id
    ).scalar_subquery()