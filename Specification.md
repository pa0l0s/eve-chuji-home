This specification provides the technical roadmap for a coding agent to build the "Grupa Operacyjna ZLY CHUJI" Corporation Portal.

Technical Specification: Corporation Portal
##1. Core Tech Stack & Requirements
Authentication: EVE SSO (OAuth 2.0).

API Integration: ESI (EVE Swagger Interface) & Janice API.

Permissions: Data visibility is restricted to authenticated members of "Grupa Operacyjna ZLY CHUJI" only.

Database: Required to store Refresh Tokens and cached Janice pricing.

##2. SSO Authentication & Scopes
The application must register at developers.eveonline.com with the following scopes:

publicData (Basic character identity)

esi-corporations.read_projects.v1 (To pull project data)

esi-wallet.read_character_wallet.v1 (Member Info page)

esi-skills.read_skills.v1 (Member Info page)

esi-characters.read_corporation_roles.v1 (To verify Project Manager permissions)

##3. Module: Homepage (Corp Project Dashboard)
The landing page displays real-time progress for active Corporation Projects.

Endpoint: GET /corporations/{corporation_id}/projects/

Functionality:

Display progress bars for active "Item Delivery" or "Manual" projects.

Janice Integration: For Buy-back projects, the agent must fetch prices from the Janice API.

Reference: Janice Link ID QcoH7M.

Formula: Price = (Janice 4-4 Instant Buy) * 0.90.

Table View: Item Name | Required Quantity | Progress (%) | Corp Buy Price (90% Jita).

##4. Module: Member Profile Page
A private dashboard for the logged-in user to see their character status.

Endpoints:

/characters/{character_id}/

/characters/{character_id}/wallet/

/characters/{character_id}/skills/

UI Elements:

Character Card: Portrait, Name, Corporation, Nation, and Security Status.

Financials: Current Wallet Balance (formatted with ISK separators).

Skill Profile: Total Skillpoints (SP) and current training queue status.

##5. Coding Agent Instructions (Prompt Segment)
Copy and paste this into your coding agent (e.g., Cursor, Claude Engineer, or GitHub Copilot):

"Act as a Full Stack Developer. Build a web application for an EVE Online Corporation.

Requirement 1: Implement EVE SSO using OAuth 2.0. Store the access_token and refresh_token securely.
Requirement 2: Create a dashboard that calls the ESI /corporations/{corp_id}/projects/ endpoint. Filter view to only show projects for the 'Grupa Operacyjna ZLY CHUJI' corp ID.
Requirement 3: Integrate the Janice API. For buyback items, calculate the value based on 90% of the 'Instant Buy' price from Jita 4-4.
Requirement 4: Create a 'Member Info' route that displays the authenticated user's Wallet Balance and Total Skillpoints.
Requirement 5: Styling should be 'EVE Dark Mode'—use deep greys, blacks, and orange highlights (Caldari/Guristas aesthetic)."

##6. Janice API Logic (Reference)
The agent should use the Janice POST /api/v1/get-pricelist or GET /api/v1/market-price endpoint.

Input: Item List from the Corp Project.

Market: 60003760 (Jita).

Logic: Result * 0.9.

Next Step for you:
Have you already created the Client ID and Secret on the EVE Developer portal? You will need to provide those to your coding agent to make the login actually work.