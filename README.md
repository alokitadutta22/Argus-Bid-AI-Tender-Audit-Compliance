# Sentinel — AI-Driven Tender Auditing & Compliance Platform

**Sentinel** is a production-grade, highly auditable, and visually spectacular AI-driven Tender Auditing & Compliance Platform built for Public Sector Undertakings (PSUs) like IOCL. It automates the tedious process of validating vendor submissions against complex Master BID/NIT (Notice Inviting Tender) documents.

## Project Structure

- `tender_audit_platform.py`: The core Streamlit application. This is the working tool that handles PDF parsing, document classification, Manufacturer's Authorization Form (MAF) validation, PQC checking, and explainable vendor ranking.
- `sentinel_showcase.html`: A static, self-contained HTML showcase website that runs in the browser. Used for presentations and demonstrations of the platform's capabilities without needing a backend server.
- `requirements.txt`: Python dependencies required to run the application.
- `run.bat`: A Windows batch script to easily install dependencies and launch the application.

## Prerequisites

- Python 3.8+ installed on your system.

## How to Run Locally

### Using the Batch Script (Windows)
Simply double-click the `run.bat` file in the project folder. It will automatically install the necessary dependencies and start the Streamlit application in your default web browser.

### Manual Installation (Cross-Platform)
1. Open a terminal and navigate to the project directory:
   ```bash
   cd path/to/TENDER-AUDIT-PLATFORM
   ```
2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the Streamlit application:
   ```bash
   streamlit run tender_audit_platform.py
   ```

## Using the Platform

1. **Load Demo Corpus:** In the application sidebar, click "Load Demo Corpus" to instantly see how the platform works with a mock IOCL NIT and three vendor submissions.
2. **Upload Master BID/NIT:** Upload the main tender document (PDF or TXT).
3. **Add Vendors:** Enter a vendor's name and upload their submission documents (PDFs, TXTs). The system can handle messy filenames.
4. **Run Full Audit:** Click the run button to parse the documents, classify them, extract metrics, and generate the compliance leaderboard and XAI (Explainable AI) rationale.
5. **LLM Augmentation (Optional):** In the sidebar, you can provide an Anthropic API key to enhance document classification for edge cases and generate an executive summary. The core compliance logic remains deterministic and rule-based for strict auditability.

## Deployment

The application is built entirely as a single-file Streamlit app. It includes a `render.yaml` Blueprint for instant 1-click deployment on Render.
- **Render:** Connect your GitHub repository to Render and use the Blueprint feature to instantly launch a secure native Python Web Service.

## Showcase Website

To present the design, architecture, and live demo capabilities quickly to stakeholders, simply open `sentinel_showcase.html` in any modern web browser. It requires no server and runs locally.
