# CEDA Status Slack Bot

A Slack bot that allows users to view CEDA services status information and authorized users to manage and update CEDA service status information directly from Slack.

## Overview

The CEDA Status Slack Bot provides an interactive interface in Slack to view and manage service status information. It integrates with GitHub to store and retrieve status information, allowing authorized team members to quickly update service statuses without leaving Slack.

## Features

- **View Current Status**: Use the `/ceda-status` command to quickly see the current status of all CEDA services.
- **Management Interface**: Use the `/ceda-status-edit` command (authorized users only) to:
  - View all services and their status
  - Add new services
  - Edit existing services
  - Delete services
  - Add, edit, or delete status updates for each service
- **Status Types**:
  - ✅ Resolved
  - ☢️ Degraded
  - ⛔️ Down
  - ⚠️ At Risk
- **GitHub Integration**: Automatically updates the status information in a GitHub repository when changes are submitted.

### Commands

- `/ceda-status` - Shows the current status of all services
- `/ceda-status-edit` - Opens the management interface (authorized users only)
