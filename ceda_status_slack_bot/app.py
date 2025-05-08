from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import os
import re
import datetime
import traceback
import requests
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Get environment variables
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

# Initialise the Slack app
app = App(token=SLACK_BOT_TOKEN)

# Global variables to store state
global_statuses = None
has_unsaved_changes = False
working_copies = {}
original_statuses = None

# List of authorised user IDs who can edit the status
AUTHORISED_USERS = os.environ.get("AUTHORISED_USERS", "").split(",")

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

logger.critical(f"Authorised users: {AUTHORISED_USERS}")


def load_status_data():
    """Load status data from GitHub and reset changes flag"""
    global global_statuses, original_statuses, has_unsaved_changes

    try:
        response = requests.get(
            "https://raw.githubusercontent.com/cedadev/ceda-status/refs/heads/main/status.json"
        )
        response.raise_for_status()
        global_statuses = response.json()

        # Store a deep copy of the original data for comparison
        import copy

        original_statuses = copy.deepcopy(global_statuses)

        has_unsaved_changes = False
        return global_statuses
    except requests.RequestException as e:
        logger.error(f"Error loading status data: {e}")
        # Return empty list if we can't load data
        return []


def has_changes():
    """Compare current statuses with original data to detect actual changes"""
    global global_statuses, original_statuses

    if original_statuses is None or global_statuses is None:
        return False

    # Simple length check first
    if len(global_statuses) != len(original_statuses):
        return True

    # Compare each service and its attributes
    import json

    # Use JSON serialization for a deep comparison
    return json.dumps(global_statuses, sort_keys=True) != json.dumps(
        original_statuses, sort_keys=True
    )


def get_status_data():
    """Get status data from global variable or load it if not available"""
    global global_statuses

    if global_statuses is None:
        return load_status_data()
    return global_statuses


def validate_date_format(date_str):
    """
    Validates if the date string is in ISO format YYYY-MM-DDThh:mm
    Returns tuple (is_valid, error_message)
    """
    # Check basic format with regex
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
    if not re.match(pattern, date_str):
        return False, "Date must be in format YYYY-MM-DDThh:mm (e.g. 2024-05-20T14:30)"

    # Check if it's a valid date and time
    try:
        # Parse the date string to ensure it's a valid date/time
        datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M")
        return True, None
    except ValueError as e:
        return False, f"Invalid date or time: {str(e)}"


def validate_url(url):
    """Basic URL validation"""
    if not url:  # Empty URLs are acceptable
        return True, None

    url_pattern = r"^https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+"
    if not re.match(url_pattern, url):
        return False, "Please enter a valid URL starting with http:// or https://"
    return True, None


def get_status_emoji(status_text):
    """Return emoji based on status"""
    status_text = status_text.lower()

    if "resolved" in status_text:
        return "✅"  # Green check for resolved
    elif "degraded" in status_text:
        return "☢️"  # Orange circle for degraded
    elif "down" in status_text:
        return "⛔️"  # Red circle for down
    elif "at risk" in status_text:
        return "⚠️"  # Yellow warning sign for at risk

    return "⚠️"  # Default warning emoji


@app.command("/ceda-status")
def ceda_status_command(ack, respond):
    """Handler for /ceda-status command"""
    ack()

    try:
        data = get_status_data()

        # Build the response message with emojis based on status
        messages = []
        for status in data:
            status_emoji = get_status_emoji(status["status"])

            # Add calendar emoji for date and speech bubble for summary
            messages.append(
                f"{status_emoji} *Service: {status['affectedServices']}* is "
                f"*{status['status']}* as of 🗓️{status['date']} 💬 {status['summary']}\n"
            )

        # Send the response back to Slack
        message = (
            "\n".join(messages) if messages else "No status information available."
        )
        respond(message)

    except Exception as e:
        respond(f"❌ Error fetching CEDA status: {str(e)}")


@app.command("/ceda-status-edit")
def open_edit_modal(ack, body, client, respond):
    """Handler for /ceda-status-edit command - opens management modal"""
    # Acknowledge command immediately
    ack()

    try:
        # Get the user ID of the person who triggered the command
        if "user_id" not in body:
            # Handle missing user ID
            respond(
                "⚠️ Error: Could not identify your user ID. Please try again or contact an administrator."
            )
            logger.error(f"Missing user_id in request body: {body}")
            return

        user_id = body["user_id"]

        # Check if user is authorised
        if user_id not in AUTHORISED_USERS:
            # Send an error message
            respond(
                "⛔ You are not authorised to edit CEDA service status. Please contact an administrator if you need access."
            )
            logger.warning(f"Unauthorised access attempt by user ID: {user_id}")
            return

        # Get status data from global variable
        statuses = get_status_data()

        # Create the header blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "CEDA Services Status Management",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Manage services and their status information:*",
                },
            },
        ]

        # Add unsaved changes warning and reset button if needed
        if has_changes():
            blocks.extend(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "⚠️ *You have unsaved changes*",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "🔄 Reset Changes",
                                    "emoji": True,
                                },
                                "style": "danger",
                                "value": "reset_changes",
                                "action_id": "reset_changes",
                                "confirm": {
                                    "title": {
                                        "type": "plain_text",
                                        "text": "Reset all changes?",
                                    },
                                    "text": {
                                        "type": "plain_text",
                                        "text": "This will discard all your unsaved changes and reload the original data from GitHub.",
                                    },
                                    "confirm": {
                                        "type": "plain_text",
                                        "text": "Yes, reset",
                                    },
                                    "deny": {"type": "plain_text", "text": "Cancel"},
                                },
                            }
                        ],
                    },
                ]
            )

        blocks.append({"type": "divider"})

        # Add a section for each service
        for i, status in enumerate(statuses):
            status_emoji = get_status_emoji(status["status"])

            # Add service info section
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{status_emoji} *{status['affectedServices']}* - {status['status']}",
                    },
                }
            )

            # Add action buttons for this service
            blocks.append(
                {
                    "type": "actions",
                    "block_id": f"service_actions_{i}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "✏️ Edit",
                                "emoji": True,
                            },
                            "value": str(i),
                            "action_id": f"edit_service_{i}",
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "🗑️ Delete",
                                "emoji": True,
                            },
                            "value": str(i),
                            "action_id": f"delete_service_{i}",
                            "style": "danger",
                            "confirm": {
                                "title": {
                                    "type": "plain_text",
                                    "text": "Delete this service?",
                                },
                                "text": {
                                    "type": "plain_text",
                                    "text": f"This will remove the service '{status['affectedServices']}' and all its updates.",
                                },
                                "confirm": {
                                    "type": "plain_text",
                                    "text": "Yes, delete",
                                },
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                    ],
                }
            )

            # Add a divider between services
            blocks.append({"type": "divider"})

        # Add button to add a new service
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "➕ Add New Service",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": "add_service",
                        "action_id": "add_service",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "💾 Submit Changes",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": "submit_changes",
                        "action_id": "submit_changes",
                        "confirm": {
                            "title": {
                                "type": "plain_text",
                                "text": "Submit changes?",
                            },
                            "text": {
                                "type": "plain_text",
                                "text": "This will submit your changes.",
                            },
                            "confirm": {
                                "type": "plain_text",
                                "text": "Yes, submit",
                            },
                            "deny": {"type": "plain_text", "text": "Cancel"},
                        },
                    },
                ],
            }
        )

        # Open the modal
        response = client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "service_list_modal",
                "title": {"type": "plain_text", "text": "CEDA Services"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": blocks,
            },
        )

        if not response["ok"]:
            logger.error(f"Error opening modal: {response.get('error')}")
            if "response_metadata" in response:
                logger.error(response["response_metadata"])

    except Exception as e:
        logger.error(f"Error opening modal: {str(e)}")
        logger.error(traceback.format_exc())
        respond(f"❌ Error: {str(e)}")


@app.action(re.compile("edit_service_\\d+"))
def handle_edit_service(ack, body, client):
    """Handler for edit service button clicks"""
    ack()

    try:
        # Extract the service index from the value
        service_index = int(body["actions"][0]["value"])
        show_service_details(body, client, service_index)
    except Exception as e:
        logger.error(f"Error handling edit service: {str(e)}")
        logger.error(traceback.format_exc())


def show_service_details(body, client, service_index):
    """Display details for a specific service"""
    try:
        # Get the current view ID
        view_id = body["container"]["view_id"]

        # Get the current service directly from global_statuses
        global global_statuses, working_copies

        # Clear any existing working copy when opening a service for editing
        if service_index in working_copies:
            del working_copies[service_index]

        if global_statuses is None:
            global_statuses = get_status_data()

        status = global_statuses[service_index]

        # Update the view with the service details
        response = update_service_view(client, view_id, service_index, status)

        if not response["ok"]:
            logger.error(f"Error updating view: {response.get('error')}")

    except Exception as e:
        logger.error(f"Error showing service details: {str(e)}")
        logger.error(traceback.format_exc())


@app.action("back_to_list")
def handle_back_to_list(ack, body, client):
    """Handler for back to list button clicks"""
    if callable(ack):
        ack()

    try:
        # Get view ID based on event type
        if "container" in body and "view_id" in body["container"]:
            view_id = body["container"]["view_id"]
        elif "view" in body and "id" in body["view"]:
            view_id = body["view"]["id"]
        else:
            logger.error("Could not find view_id in the standard locations")
            if "callback_id" in body:
                logger.error(f"Found callback_id: {body['callback_id']}")

            # Send direct message as fallback
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="⚠️ Could not update the view. Please try running the `/ceda-status-edit` command again.",
            )
            return

        # Fetch current status data
        statuses = get_status_data()

        # Create header blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "CEDA Services Status Management",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Manage services and their status information:*",
                },
            },
        ]

        # Check for changes and add warning if needed
        if has_changes():
            blocks.extend(
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "⚠️ *You have unsaved changes*",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "🔄 Reset Changes",
                                    "emoji": True,
                                },
                                "style": "danger",
                                "value": "reset_changes",
                                "action_id": "reset_changes",
                                "confirm": {
                                    "title": {
                                        "type": "plain_text",
                                        "text": "Reset all changes?",
                                    },
                                    "text": {
                                        "type": "plain_text",
                                        "text": "This will discard all your unsaved changes and reload the original data from GitHub.",
                                    },
                                    "confirm": {
                                        "type": "plain_text",
                                        "text": "Yes, reset",
                                    },
                                    "deny": {"type": "plain_text", "text": "Cancel"},
                                },
                            }
                        ],
                    },
                ]
            )

        blocks.append({"type": "divider"})

        # Add a section for each service
        for i, status in enumerate(statuses):
            status_emoji = get_status_emoji(status["status"])

            # Add service info section
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{status_emoji} *{status['affectedServices']}* - {status['status']}",
                    },
                }
            )

            # Add action buttons
            blocks.append(
                {
                    "type": "actions",
                    "block_id": f"service_actions_{i}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "✏️ Edit",
                                "emoji": True,
                            },
                            "value": str(i),
                            "action_id": f"edit_service_{i}",
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "🗑️ Delete",
                                "emoji": True,
                            },
                            "value": str(i),
                            "action_id": f"delete_service_{i}",
                            "style": "danger",
                            "confirm": {
                                "title": {
                                    "type": "plain_text",
                                    "text": "Delete this service?",
                                },
                                "text": {
                                    "type": "plain_text",
                                    "text": f"This will remove the service '{status['affectedServices']}' and all its updates.",
                                },
                                "confirm": {
                                    "type": "plain_text",
                                    "text": "Yes, delete",
                                },
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                    ],
                }
            )

            # Add divider between services
            blocks.append({"type": "divider"})

        # Add button to add a new service
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "➕ Add New Service",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": "add_service",
                        "action_id": "add_service",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "💾 Submit Changes",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": "submit_changes",
                        "action_id": "submit_changes",
                        "confirm": {
                            "title": {
                                "type": "plain_text",
                                "text": "Submit changes?",
                            },
                            "text": {
                                "type": "plain_text",
                                "text": "This will submit your changes.",
                            },
                            "confirm": {
                                "type": "plain_text",
                                "text": "Yes, submit",
                            },
                            "deny": {"type": "plain_text", "text": "Cancel"},
                        },
                    },
                ],
            }
        )

        # Update the view
        response = client.views_update(
            view_id=view_id,
            view={
                "type": "modal",
                "callback_id": "service_list_modal",
                "title": {"type": "plain_text", "text": "CEDA Services"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": blocks,
            },
        )

        if not response["ok"]:
            logger.error(f"Error updating view to go back: {response.get('error')}")
            if "response_metadata" in response:
                logger.error(response["response_metadata"])

    except Exception as e:
        logger.error(f"Error going back to list: {str(e)}")
        logger.error(traceback.format_exc())


@app.view(re.compile("edit_service_modal.*"))
def handle_edit_service_submission(ack, body, view, client):
    """Handler for edit service form submissions"""
    errors = {}

    try:
        global global_statuses, has_unsaved_changes

        # Extract service index from metadata
        metadata = view["private_metadata"].split(":")
        service_index = int(metadata[0])

        # Find block IDs by action IDs
        service_name_block = None
        service_status_block = None
        service_date_block = None
        service_summary_block = None

        # Extract block IDs from view state
        for block_id, actions in view["state"]["values"].items():
            for action_id, value in actions.items():
                if action_id == "service_name_input":
                    service_name_block = block_id
                elif action_id == "service_status_select":
                    service_status_block = block_id
                elif action_id == "service_date_input":
                    service_date_block = block_id
                elif action_id == "service_summary_input":
                    service_summary_block = block_id

        # Ensure we found all needed blocks
        if not all(
            [
                service_name_block,
                service_status_block,
                service_date_block,
                service_summary_block,
            ]
        ):
            raise ValueError("Could not find all required form fields")

        # Extract the service details
        service_name = view["state"]["values"][service_name_block][
            "service_name_input"
        ]["value"]
        service_status = view["state"]["values"][service_status_block][
            "service_status_select"
        ]["selected_option"]["text"]["text"]
        service_date = view["state"]["values"][service_date_block][
            "service_date_input"
        ]["value"]
        service_summary = view["state"]["values"][service_summary_block][
            "service_summary_input"
        ]["value"]

        # Create service object
        updated_service = {
            "status": service_status,
            "affectedServices": service_name,
            "summary": service_summary,
            "date": service_date,
            "updates": [],
        }

        # Validate the main date
        is_valid, error_msg = validate_date_format(service_date)
        if not is_valid:
            errors[service_date_block] = error_msg

        # Find update blocks by action ID
        update_blocks = {}
        update_details_blocks = {}
        update_url_blocks = {}

        for block_id, actions in view["state"]["values"].items():
            for action_id, value in actions.items():
                if action_id == "update_date_input":
                    match = re.match(r"update_(\d+)_date", block_id)
                    if match:
                        idx = match.group(1)
                        update_blocks[idx] = block_id
                elif action_id == "update_details_input":
                    match = re.match(r"update_(\d+)_details", block_id)
                    if match:
                        idx = match.group(1)
                        update_details_blocks[idx] = block_id
                elif action_id == "update_url_input":
                    match = re.match(r"update_(\d+)_url", block_id)
                    if match:
                        idx = match.group(1)
                        update_url_blocks[idx] = block_id

        # Process updates
        for idx in sorted(update_blocks.keys()):
            if idx in update_details_blocks:
                update_block = update_blocks[idx]
                update_details_block = update_details_blocks[idx]

                update_date = view["state"]["values"][update_block][
                    "update_date_input"
                ]["value"]
                update_details = view["state"]["values"][update_details_block][
                    "update_details_input"
                ]["value"]

                # Validate update date
                is_valid, error_msg = validate_date_format(update_date)
                if not is_valid:
                    errors[update_block] = error_msg
                    continue

                # Create the update
                update = {"date": update_date, "details": update_details}

                # Add URL if provided
                if idx in update_url_blocks:
                    update_url_block = update_url_blocks[idx]
                    url_value = view["state"]["values"][update_url_block][
                        "update_url_input"
                    ]["value"]
                    if url_value:
                        is_valid, error_msg = validate_url(url_value)
                        if not is_valid:
                            errors[update_url_block] = error_msg
                            continue
                        update["url"] = url_value

                # Add the update to the service
                updated_service["updates"].append(update)

        # Basic validations
        if not updated_service["updates"]:
            errors[service_summary_block] = "At least one update is required"

        if not service_name:
            errors[service_name_block] = "Service name cannot be empty"

        if not service_summary:
            errors[service_summary_block] = "Summary cannot be empty"

        # If there are validation errors, acknowledge with errors
        if errors:
            ack(response_action="errors", errors=errors)
            return

        # No validation errors, continue with normal ack
        ack()

        # Update the service in the global variable
        global_statuses[service_index] = updated_service
        has_unsaved_changes = True

        # Clean up working copy if it exists
        if service_index in working_copies:
            del working_copies[service_index]

        # Show updated list or send confirmation message
        if "trigger_id" in body:
            open_edit_modal(lambda: None, body, client)
        else:
            # If no trigger_id, just show a message
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="✅ Service updated successfully! Run `/ceda-status-edit` to see the updated list.",
            )

    except Exception as e:
        if not errors:
            ack()

        logger.error(f"Error updating service: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error updating service: {str(e)}"
        )


@app.event("view_closed")
def handle_any_view_closed(body, logger, client):
    """Handler for modal close events"""

    try:
        # Extract user ID and view callback ID
        user_id = body["user"]["id"]
        view_callback_id = body["view"]["callback_id"]

        # Check which modal was closed
        if view_callback_id == "edit_service_modal":
            logger.info("Edit service modal was closed, returning to services list")

            # Try to reopen the main list view if possible
            if "trigger_id" in body:
                # Open a new modal with the list view
                open_edit_modal(lambda: None, body, client)

                # Send a message with a button as fallback
                client.chat_postMessage(
                    channel=user_id,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Click the button below to see the services list:",
                            },
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "View Services"},
                                "action_id": "view_services_from_message",
                            },
                        }
                    ],
                )

    except Exception as e:
        logger.error(f"Error handling view closed: {e}")
        logger.error(traceback.format_exc())


@app.action("add_update_from_detail")
def handle_add_update(ack, body, client):
    """Handler for add update button clicks"""
    ack()

    try:
        # Extract service index from the button's value
        service_index = int(body["actions"][0]["value"])
        view_id = body["container"]["view_id"]

        global global_statuses, working_copies

        # Special handling for new service (index -1)
        if service_index == -1:
            # For a new service, check if we need to extract current form data
            if -1 not in working_copies or not working_copies[-1].get("updates"):
                # Create base working copy with default values
                working_copy = {
                    "status": "resolved",
                    "affectedServices": "",
                    "summary": "",
                    "date": "",
                    "updates": [],
                }

                # Extract values from the current view state if available
                try:
                    if (
                        "view" in body
                        and "state" in body["view"]
                        and "values" in body["view"]["state"]
                    ):
                        state = body["view"]["state"]
                        for block_id, actions in state["values"].items():
                            for action_id, value in actions.items():
                                if (
                                    action_id == "service_name_input"
                                    and "value" in value
                                ):
                                    working_copy["affectedServices"] = (
                                        value["value"] or ""
                                    )
                                elif (
                                    action_id == "service_status_select"
                                    and "selected_option" in value
                                ):
                                    working_copy["status"] = value[
                                        "selected_option"
                                    ].get("value", "resolved")
                                elif (
                                    action_id == "service_date_input"
                                    and "value" in value
                                ):
                                    working_copy["date"] = value["value"] or ""
                                elif (
                                    action_id == "service_summary_input"
                                    and "value" in value
                                ):
                                    working_copy["summary"] = value["value"] or ""
                except Exception as e:
                    logger.error(f"Error extracting view state from body: {e}")
                    # Use existing working copy if available
                    if -1 in working_copies:
                        working_copy = working_copies[-1]
            else:
                # Use existing working copy
                working_copy = working_copies[-1]
        # Normal handling for existing services
        elif service_index in working_copies:
            working_copy = working_copies[service_index]
        else:
            service = global_statuses[service_index]
            working_copy = service.copy()
            if "updates" in working_copy:
                working_copy["updates"] = [
                    update.copy() for update in service["updates"]
                ]
            else:
                working_copy["updates"] = []

        # Make sure updates array exists
        working_copy.setdefault("updates", [])

        # Add a new empty update
        new_update = {"date": "", "details": "", "url": ""}
        working_copy["updates"].append(new_update)

        # Store the updated working copy
        working_copies[service_index] = working_copy

        # Generate unique suffix for block IDs
        unique_suffix = str(int(datetime.datetime.now().timestamp()))

        # Create blocks for the updated view
        blocks = create_service_detail_blocks(
            service_index, working_copy, unique_suffix
        )

        # Determine title and callback_id based on whether this is a new service or editing
        title = "Add New Service" if service_index == -1 else "Edit Service"
        callback_id = (
            f"add_service_modal_{unique_suffix}"
            if service_index == -1
            else f"edit_service_modal_{service_index}_{unique_suffix}"
        )
        submit_text = "Create Service" if service_index == -1 else "Save Changes"

        # Update the view
        client.views_update(
            view_id=view_id,
            view={
                "type": "modal",
                "callback_id": callback_id,
                "title": {"type": "plain_text", "text": title},
                "submit": {"type": "plain_text", "text": submit_text},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": f"{service_index}:0",
                "blocks": blocks,
            },
        )

    except Exception as e:
        logger.error(f"Error adding update: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error adding update: {str(e)}"
        )


@app.action("reset_changes")
def handle_reset_changes(ack, body, client):
    """Handler for reset changes button clicks"""
    ack()

    try:
        # Reset global data by reloading from GitHub
        load_status_data()

        # Clear working copies
        global working_copies
        working_copies.clear()

        # Refresh the view
        handle_back_to_list(lambda: None, body, client)

    except Exception as e:
        logger.error(f"Error resetting changes: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error resetting changes: {str(e)}"
        )


@app.action(re.compile("delete_update_\\d+"))
def handle_delete_update(ack, body, client):
    """Handler for delete update button clicks"""
    ack()
    try:
        value = body["actions"][0]["value"]
        service_index, update_index = map(int, value.split(":"))

        global global_statuses, working_copies

        # Get or create working copy
        if service_index in working_copies:
            working_copy = working_copies[service_index]
        else:
            service = global_statuses[service_index]
            working_copy = service.copy()
            if "updates" in working_copy:
                working_copy["updates"] = [
                    update.copy() for update in service["updates"]
                ]
            else:
                working_copy["updates"] = []

        # Remove the update if it exists
        if "updates" in working_copy and update_index < len(working_copy["updates"]):
            working_copy["updates"].pop(update_index)
            working_copies[service_index] = working_copy

            # Generate unique suffix for block IDs
            unique_suffix = str(int(datetime.datetime.now().timestamp()))

            # Update the view
            blocks = create_service_detail_blocks(
                service_index, working_copy, unique_suffix
            )
            view_id = body["container"]["view_id"]

            client.views_update(
                view_id=view_id,
                view={
                    "type": "modal",
                    "callback_id": f"edit_service_modal_{service_index}_{unique_suffix}",
                    "title": {"type": "plain_text", "text": "Edit Service"},
                    "submit": {"type": "plain_text", "text": "Save Changes"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "private_metadata": f"{service_index}:0",
                    "blocks": blocks,
                },
            )
        else:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text=f"❌ Update not found. Index: {update_index}, Total updates: {len(working_copy.get('updates', []))}",
            )
    except Exception as e:
        logger.error(f"Error deleting update: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error deleting update: {str(e)}"
        )


def create_service_detail_blocks(service_index, service, unique_suffix=""):
    """Create UI blocks for service detail view with proper indexing"""
    # Map the status to ensure it matches one of our options
    status_text = str(service.get("status", "")).lower()

    # Determine the appropriate status mapping
    mapped_status = "Resolved"  # Default
    mapped_value = "resolved"

    if "degraded" in status_text:
        mapped_status = "Degraded"
        mapped_value = "degraded"
    elif "down" in status_text:
        mapped_status = "Down"
        mapped_value = "down"
    elif "at risk" in status_text:
        mapped_status = "At Risk"
        mapped_value = "at risk"

    # Ensure all values are strings with defaults
    service_name = str(service.get("affectedServices", ""))
    date_value = str(service.get("date", ""))
    summary_value = str(service.get("summary", ""))

    # Use appropriate header based on whether editing or creating
    header_text = f"Edit {service_name}" if service_name else "Add New Service"

    # Create header blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True,
            },
        },
        {"type": "divider"},
    ]

    # Create back button with confirmation
    back_button = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": "← Back to Service List",
            "emoji": True,
        },
        "value": "back_to_list",
        "action_id": "back_to_list_from_service",
        "confirm": {
            "title": {
                "type": "plain_text",
                "text": "Exit Page?",
            },
            "text": {
                "type": "plain_text",
                "text": "You may lose changes if you go back. Continue?",
            },
            "confirm": {
                "type": "plain_text",
                "text": "Yes, exit",
            },
            "deny": {"type": "plain_text", "text": "Cancel"},
        },
    }

    # Add the actions block with the back button
    blocks.append({"type": "actions", "elements": [back_button]})

    # Add the main service fields
    blocks.extend(
        [
            {
                "type": "input",
                "block_id": f"service_name_{unique_suffix}",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "service_name_input",
                    "initial_value": service_name,
                },
                "label": {"type": "plain_text", "text": "Service Name"},
            },
            {
                "type": "input",
                "block_id": f"service_status_{unique_suffix}",
                "element": {
                    "type": "static_select",
                    "action_id": "service_status_select",
                    "initial_option": {
                        "text": {"type": "plain_text", "text": mapped_status},
                        "value": mapped_value,
                    },
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Resolved"},
                            "value": "resolved",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Degraded"},
                            "value": "degraded",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Down"},
                            "value": "down",
                        },
                        {
                            "text": {"type": "plain_text", "text": "At Risk"},
                            "value": "at risk",
                        },
                    ],
                },
                "label": {"type": "plain_text", "text": "Status"},
            },
            {
                "type": "input",
                "block_id": f"service_date_{unique_suffix}",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "service_date_input",
                    "initial_value": date_value,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Example: 2024-05-20T14:30",
                    },
                },
                "hint": {
                    "type": "plain_text",
                    "text": "Use ISO format: YYYY-MM-DDThh:mm",
                },
                "label": {"type": "plain_text", "text": "Date and Time"},
            },
            {
                "type": "input",
                "block_id": f"service_summary_{unique_suffix}",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "service_summary_input",
                    "multiline": True,
                    "initial_value": summary_value,
                },
                "label": {"type": "plain_text", "text": "Summary"},
            },
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📝 Updates",
                    "emoji": True,
                },
            },
        ]
    )

    # Add blocks for each update
    if service.get("updates"):
        for j, update in enumerate(service["updates"]):
            # Ensure empty strings instead of None values
            update_date = str(update.get("date", ""))
            update_details = str(update.get("details", ""))
            update_url = str(update.get("url", ""))

            blocks.extend(
                [
                    {
                        "type": "section",
                        "block_id": f"update_section_{j}_{unique_suffix}",
                        "text": {"type": "mrkdwn", "text": f"*Update #{j+1}*"},
                    },
                    {
                        "type": "input",
                        "block_id": f"update_{j}_date_{unique_suffix}",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "update_date_input",
                            "initial_value": update_date,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Example: 2024-05-20T14:30",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Date"},
                        "hint": {
                            "type": "plain_text",
                            "text": "Use ISO format: YYYY-MM-DDThh:mm",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": f"update_{j}_details_{unique_suffix}",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "update_details_input",
                            "multiline": True,
                            "initial_value": update_details,
                        },
                        "label": {"type": "plain_text", "text": "Details"},
                    },
                    {
                        "type": "input",
                        "block_id": f"update_{j}_url_{unique_suffix}",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "update_url_input",
                            "initial_value": update_url,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Example: https://example.com",
                            },
                        },
                        "label": {"type": "plain_text", "text": "URL"},
                        "hint": {
                            "type": "plain_text",
                            "text": "Optional link to more information or related resources",
                        },
                        "optional": True,
                    },
                    {
                        "type": "actions",
                        "block_id": f"update_{j}_actions_{unique_suffix}",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "🗑️ Delete Update",
                                    "emoji": True,
                                },
                                "style": "danger",
                                "value": f"{service_index}:{j}",
                                "action_id": f"delete_update_{j}",
                                "confirm": {
                                    "title": {
                                        "type": "plain_text",
                                        "text": "Delete this update?",
                                    },
                                    "text": {
                                        "type": "plain_text",
                                        "text": "This will remove this update from the service.",
                                    },
                                    "confirm": {
                                        "type": "plain_text",
                                        "text": "Yes, delete",
                                    },
                                    "deny": {"type": "plain_text", "text": "Cancel"},
                                },
                            }
                        ],
                    },
                ]
            )

            # Add divider between updates
            if j < len(service["updates"]) - 1:
                blocks.append({"type": "divider"})
    else:
        # Show message when no updates exist
        blocks.append(
            {
                "type": "section",
                "block_id": f"no_updates_{unique_suffix}",
                "text": {"type": "mrkdwn", "text": "No updates for this service yet."},
            }
        )

    # Add "Add New Update" button
    blocks.append(
        {
            "type": "actions",
            "block_id": f"update_actions_{unique_suffix}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Add New Update",
                        "emoji": True,
                    },
                    "value": str(service_index),
                    "action_id": "add_update_from_detail",
                }
            ],
        }
    )

    return blocks


def update_service_view(client, view_id, service_index, status):
    """Update the service view with the given status data"""
    # Create blocks with a unique timestamp suffix
    blocks = create_service_detail_blocks(
        service_index,
        status,
        unique_suffix=str(int(datetime.datetime.now().timestamp())),
    )

    # Update the view with a unique callback_id to prevent caching
    response = client.views_update(
        view_id=view_id,
        view={
            "type": "modal",
            "callback_id": f"edit_service_modal_{int(datetime.datetime.now().timestamp())}",
            "title": {"type": "plain_text", "text": "Edit Service"},
            "submit": {"type": "plain_text", "text": "Save Changes"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": f"{service_index}:0",
            "blocks": blocks,
        },
    )

    return response


@app.action(re.compile("delete_service_\\d+"))
def handle_delete_service(ack, body, client):
    """Handler for delete service button clicks"""
    ack()
    try:
        # Extract the service index from the button value
        service_index = int(body["actions"][0]["value"])

        global global_statuses, has_unsaved_changes, working_copies

        # Get the service name for confirmation
        service_name = global_statuses[service_index]["affectedServices"]

        # Remove the service if valid index
        if 0 <= service_index < len(global_statuses):
            global_statuses.pop(service_index)
            has_unsaved_changes = True

            # Remove any working copy for this service
            if service_index in working_copies:
                del working_copies[service_index]

            # Refresh the view
            handle_back_to_list(lambda: None, body, client)
        else:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text=f"❌ Service index out of range: {service_index}, Total services: {len(global_statuses)}",
            )
    except Exception as e:
        logger.error(f"Error deleting service: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error deleting service: {str(e)}"
        )


@app.action("add_service")
def handle_add_service(ack, body, client):
    """Handler for add service button clicks"""
    ack()
    try:
        # Get the current view ID
        view_id = body["container"]["view_id"]

        # Create an empty service template
        empty_service = {
            "status": "resolved",
            "affectedServices": "",
            "summary": "",
            "date": "",
            "updates": [],
        }

        # Clear any existing working copy for new service
        global working_copies
        if -1 in working_copies:
            del working_copies[-1]

        # Create unique suffix for block IDs
        unique_suffix = str(int(datetime.datetime.now().timestamp()))

        # Create blocks for the new service form
        blocks = create_service_detail_blocks(-1, empty_service, unique_suffix)

        # Update view with the new service form
        client.views_update(
            view_id=view_id,
            view={
                "type": "modal",
                "callback_id": f"add_service_modal_{unique_suffix}",
                "title": {"type": "plain_text", "text": "Add New Service"},
                "submit": {"type": "plain_text", "text": "Create Service"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": "-1:0",
                "blocks": blocks,
            },
        )

    except Exception as e:
        logger.error(f"Error adding new service: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error adding new service: {str(e)}"
        )


@app.view(re.compile("add_service_modal.*"))
def handle_add_service_submission(ack, body, view, client):
    """Handler for add service form submissions"""
    errors = {}

    try:
        global global_statuses, has_unsaved_changes

        # Find block IDs by action IDs
        service_name_block = None
        service_status_block = None
        service_date_block = None
        service_summary_block = None

        # Extract block IDs from view state
        for block_id, actions in view["state"]["values"].items():
            for action_id, value in actions.items():
                if action_id == "service_name_input":
                    service_name_block = block_id
                elif action_id == "service_status_select":
                    service_status_block = block_id
                elif action_id == "service_date_input":
                    service_date_block = block_id
                elif action_id == "service_summary_input":
                    service_summary_block = block_id

        # Make sure we found all needed blocks
        if not all(
            [
                service_name_block,
                service_status_block,
                service_date_block,
                service_summary_block,
            ]
        ):
            raise ValueError("Could not find all required form fields")

        # Extract the service details
        service_name = view["state"]["values"][service_name_block][
            "service_name_input"
        ]["value"]
        service_status = view["state"]["values"][service_status_block][
            "service_status_select"
        ]["selected_option"]["text"]["text"]
        service_date = view["state"]["values"][service_date_block][
            "service_date_input"
        ]["value"]
        service_summary = view["state"]["values"][service_summary_block][
            "service_summary_input"
        ]["value"]

        # Create new service object
        new_service = {
            "status": service_status,
            "affectedServices": service_name,
            "summary": service_summary,
            "date": service_date,
            "updates": [],
        }

        # Validate the main date
        is_valid, error_msg = validate_date_format(service_date)
        if not is_valid:
            errors[service_date_block] = error_msg

        # Find update blocks by action ID
        update_blocks = {}
        update_details_blocks = {}
        update_url_blocks = {}

        # Extract form field block IDs
        for block_id, actions in view["state"]["values"].items():
            for action_id, value in actions.items():
                if action_id == "update_date_input":
                    match = re.match(r"update_(\d+)_date", block_id)
                    if match:
                        idx = match.group(1)
                        update_blocks[idx] = block_id
                elif action_id == "update_details_input":
                    match = re.match(r"update_(\d+)_details", block_id)
                    if match:
                        idx = match.group(1)
                        update_details_blocks[idx] = block_id
                elif action_id == "update_url_input":
                    match = re.match(r"update_(\d+)_url", block_id)
                    if match:
                        idx = match.group(1)
                        update_url_blocks[idx] = block_id

        # Process all updates
        for idx in sorted(update_blocks.keys()):
            if idx in update_details_blocks:
                update_block = update_blocks[idx]
                update_details_block = update_details_blocks[idx]

                update_date = view["state"]["values"][update_block][
                    "update_date_input"
                ]["value"]
                update_details = view["state"]["values"][update_details_block][
                    "update_details_input"
                ]["value"]

                # Validate update date
                is_valid, error_msg = validate_date_format(update_date)
                if not is_valid:
                    errors[update_block] = error_msg
                    continue

                # Create the update
                update = {"date": update_date, "details": update_details}

                # Add URL if provided
                if idx in update_url_blocks:
                    update_url_block = update_url_blocks[idx]
                    url_value = view["state"]["values"][update_url_block][
                        "update_url_input"
                    ]["value"]
                    if url_value:
                        is_valid, error_msg = validate_url(url_value)
                        if not is_valid:
                            errors[update_url_block] = error_msg
                            continue
                        update["url"] = url_value

                # Add the update to the service
                new_service["updates"].append(update)

        # Basic validations
        if not new_service["updates"]:
            errors[service_summary_block] = "At least one update is required"

        if not service_name:
            errors[service_name_block] = "Service name cannot be empty"

        if not service_summary:
            errors[service_summary_block] = "Summary cannot be empty"

        # If there are validation errors, acknowledge with errors
        if errors:
            ack(response_action="errors", errors=errors)
            return

        # No validation errors, continue with normal ack
        ack()

        # Add the new service to global_statuses
        global_statuses.append(new_service)
        has_unsaved_changes = True

        # Clean up working copy
        if -1 in working_copies:
            del working_copies[-1]

        # Show updated list or send confirmation message
        if "trigger_id" in body:
            open_edit_modal(lambda: None, body, client)
        else:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="✅ Service created successfully! Run `/ceda-status-edit` to see the updated list.",
            )

    except Exception as e:
        if not errors:
            ack()

        logger.error(f"Error creating new service: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error creating new service: {str(e)}"
        )


@app.action("back_to_list_from_service")
def handle_back_to_list_from_service(ack, body, client):
    """Handler for back to list button clicks from service detail view"""
    ack()

    # Clean up any temporary working copy for new service
    global working_copies
    if -1 in working_copies:
        del working_copies[-1]

    # Call the existing back to list handler
    handle_back_to_list(lambda: None, body, client)


@app.action("submit_changes")
def handle_submit_changes(ack, body, client):
    """Handler for submit changes button clicks"""
    ack()

    try:
        global global_statuses, has_unsaved_changes

        # Check if there are changes to submit
        if not has_changes():
            client.chat_postMessage(
                channel=body["user"]["id"], text="ℹ️ No changes detected to submit."
            )
            return

        # Get GitHub token from environment
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="❌ GitHub token not found in environment variables.",
            )
            return

        # User information for commit
        user_id = body["user"]["id"]
        user_info = client.users_info(user=user_id)
        user_name = user_info["user"]["real_name"] if user_info["ok"] else user_id

        # Prepare the JSON content
        import json

        content = json.dumps(global_statuses, indent=2)

        # GitHub API details
        repo_owner = "cedadev"
        repo_name = "ceda-status"
        file_path = "status.json"
        branch = "main"

        # First, get the current file to get its SHA
        import base64
        import requests

        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Get current file info (including SHA)
        r = requests.get(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}?ref={branch}",
            headers=headers,
        )

        if r.status_code != 200:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text=f"❌ Error getting file from GitHub: {r.status_code} - {r.json().get('message', '')}",
            )
            return

        current_file = r.json()
        sha = current_file["sha"]

        # Update the file
        update_data = {
            "message": f"Update status (via Slack by {user_name})",
            "content": base64.b64encode(content.encode()).decode(),
            "sha": sha,
            "branch": branch,
        }

        r = requests.put(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}",
            headers=headers,
            json=update_data,
        )

        if r.status_code not in [200, 201]:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text=f"❌ Error updating file on GitHub: {r.status_code} - {r.json().get('message', '')}",
            )
            return

        # Reset changes flag after successful submission
        has_unsaved_changes = False

        # Send confirmation to the user
        client.chat_postMessage(
            channel=body["user"]["id"],
            text=f"✅ Status page has been successfully updated on GitHub.\n"
            f"View changes: https://github.com/{repo_owner}/{repo_name}/blob/{branch}/{file_path}",
        )

        # Close the modal by pushing a simple view that automatically closes
        view_id = body["container"]["view_id"]
        client.views_update(
            view_id=view_id,
            view={
                "type": "modal",
                "callback_id": "close_modal",
                "title": {"type": "plain_text", "text": "Changes Submitted"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "✅ Changes have been submitted successfully to GitHub!",
                        },
                    }
                ],
            },
        )

    except Exception as e:
        logger.error(f"Error submitting changes: {str(e)}")
        logger.error(traceback.format_exc())
        client.chat_postMessage(
            channel=body["user"]["id"], text=f"❌ Error submitting changes: {str(e)}"
        )


def main():
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
