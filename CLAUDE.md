# Project Context
This project is a data extraction pipeline designed to scrape Quebec CEGEP grades, class averages, and R-Scores from social media (Reddit, etc.) to reverse-engineer the Ministry of Education's IDGZ and ISGZ variables. The target output is a clean CSV dataset for a startup called Learnly.

# Tech Stack & Infrastructure
- Language: Python 3.11+
- Key Libraries: PRAW (Reddit API), pandas, python-dotenv, re (regex)
- Deployment: Docker container running as a cron job on a CasaOS (Raspberry Pi/ARM64) compute cluster.

# Agent Behavioral Rules
1. **Never Hallucinate Data:** Only write scripts that pull real data from live APIs. Never generate mock data unless explicitly asked to test a parsing function.
2. **Defensive Scraping:** Social media APIs have rate limits. Always implement `time.sleep()`, error handling (`try/except` blocks), and logging for API requests. Do not write scripts that will immediately trigger an IP ban.
3. **Bilingual Data Handling:** Quebec students use French and English interchangeably. Regex patterns must account for terms like "cote r", "r-score", "moyenne", "average", "CRC", and variations in spacing/punctuation.
4. **Environment Variables:** Never hardcode API keys or passwords. Always use `os.getenv()` and assume credentials exist in a local `.env` file.
5. **ARM64 Compatibility:** When writing the Dockerfile, ensure the base image and dependencies are compatible with linux/arm64 architecture for deployment on Raspberry Pi compute nodes.
6. **Preserve Logic:** Do not overwrite working regex pipelines or API authentication functions just to add a new feature. Modularize code by creating helper functions instead of modifying the core extraction loop.
7. **File Outputs:** Ensure all scraped data is appended to a structured CSV file with headers: `source`, `timestamp`, `program_or_course`, `grade`, `class_average`, `r_score`, `raw_text`.