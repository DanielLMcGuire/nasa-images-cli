# nasa-images-cli

## Usage

1. Search

    ```bash
    $ python nasa-images.py search "artemis II"
    Found 14 album name(s) for "artemis II":

      # Album_ID
          # e.g. "Image Title"
      2025_NASA_Photographer_Of_The_Year_Winners
          e.g. "Artemis II Countdown Demonstration Test"
      20260328_ArtemisII_countdown_pretest_meeting
          e.g. "Artemis II Preflight"
      Artemis_II
          e.g. "Artemis II launch "
          e.g. "Artemis II on Launch Pad"
      ...

    # This is suggested based on closest match to query
    Download with:
    python nasa-images.py download "Artemis_II"

    $ python nasa-images.py search artemis2
    No results for "artemis2" — retrying as "artemis II" ...
    Found 14 album name(s) for "artemis II":
    ...
    ```

1. Downloading

    ```bash
    $ python nasa-images.py download "Artemis_2"
    PS E:\src\nasa-images-cli> python nasa-images.py download "Artemis_II"
    Album      : Artemis_II
    Output dir : Artemis_II
    Total items: 3181  (32 page(s))

      # STATUS (quality) NAME
      OK (~large)  KSC-20260417-PH-KLS01_0006~orig.jpg
      ...
    ```
