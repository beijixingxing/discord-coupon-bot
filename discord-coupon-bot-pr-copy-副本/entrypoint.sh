#!/bin/sh
set -e

# Give ownership of the mounted volumes to the appuser.
# This is necessary because the container starts as root to perform this action,
# but the application itself will run as the non-root 'appuser'.
echo "Updating permissions for data and backup directories..."
chown -R appuser:appgroup /app/data
chown -R appuser:appgroup /app/backups
echo "Permissions updated."

# Drop privileges and execute the main command (passed from Dockerfile's CMD).
# "$*" expands the CMD array into a single string, which is what "sh -c" expects.
echo "Executing command as appuser: $*"
exec su appuser -s /bin/sh -c "$*"