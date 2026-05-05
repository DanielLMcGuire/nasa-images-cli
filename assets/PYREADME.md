# nasa-images-cli

Usage:

python3 nasa_tool.py search "Apollo 11"
--limit: Change the number of results shown (default: 10).
--pages: Change the search depth (default: 5).

python3 nasa_tool.py download "Apollo_11"
-o, --output: Specify a custom directory name (default is the album name).

- Search NASA image library by keyword:
  - Handles variations like spaces, underscores
  - Converts numbers to Roman numerals if no results are found and numbers are present
  - Fuzzy matching and ranking using token overlap and sequence similarity
- Bulk download albums by ID (or via prompt after search)
  - Tries multiple image sizes from highest to lowest quality
  - Skips already downloaded files, restarts failed downloads
  - Saves a list of all downloaded image URLs to a text file
  - Windows Terminal / Ghostty progress/spinner support
  - Concurrent downloads