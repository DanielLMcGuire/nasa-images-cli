# nasa-images-cli

![pypi version](https://img.shields.io/pypi/v/nasa-images-cli) ![PyPI - Downloads](https://img.shields.io/pypi/dw/nasa-images-cli)


```bash
$ pip install nasa-images-cli
$ nasa-images search <QUERY>
...
$ nasa-images download <ALBUM_ID> (-o OUTPATH)
...
```

![UI](./assets/out.gif?f=null)

> **`Missing:`** indicates images which have no URL listed in the metadata. (or videos)

## Features

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
