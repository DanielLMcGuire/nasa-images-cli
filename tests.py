import unittest
from unittest.mock import patch, MagicMock, mock_open
import urllib.error

import nasa_images

class TestNasaUtilityFunctions(unittest.TestCase):
    
    def test_roman_conversions(self):
        self.assertEqual(nasa_images.to_roman(1969), 'MCMLXIX')
        self.assertEqual(nasa_images.to_roman(11), 'XI')
        self.assertEqual(nasa_images.from_roman('MCMLXIX'), 1969)
        self.assertEqual(nasa_images.from_roman('XI'), 11)

    def test_text_replacements(self):
        self.assertEqual(nasa_images.arabic_to_roman("Apollo 11"), "Apollo XI")
        self.assertEqual(nasa_images.arabic_to_roman("Gemini 4"), "Gemini IV")
        self.assertEqual(nasa_images.roman_to_arabic("Apollo XI"), "Apollo 11")
        self.assertEqual(nasa_images.roman_to_arabic("Normal Text"), "Normal Text") 

    def test_normalize_search_text(self):
        self.assertEqual(nasa_images._normalize_search_text("Apollo_11"), "apollo 11")
        self.assertEqual(nasa_images._normalize_search_text("camelCaseText"), "camel case text")
        self.assertEqual(nasa_images._normalize_search_text(" Special  Chars!!  "), "special chars")

    def test_similarity_score(self):
        exact = nasa_images._similarity("apollo 11", "Apollo 11")
        self.assertGreater(exact, 0.9)
      
        diff = nasa_images._similarity("moon landing", "mars rover")
        self.assertLess(diff, 0.3)

    def test_item_field_extraction(self):
        mock_item = {"data": [{"title": "Test Title", "nasa_id": "12345"}]}
        self.assertEqual(nasa_images._item_field(mock_item, "title"), "Test Title")
        self.assertEqual(nasa_images._item_field(mock_item, "missing_key", "default"), "default")
        
        self.assertIsNone(nasa_images._item_field({}, "title"))

class TestMediaProcessing(unittest.TestCase):

    def test_asset_url_formatting(self):
        url = "http://example.com/path/to/my video space.mp4"
        expected = nasa_images.ASSET_BASE + "/path/to/my%20video%20space.mp4"
        self.assertEqual(nasa_images._asset_url(url), expected)

    def test_rank_video_urls(self):
        urls = [
            "http://example.com/vid~mobile.mp4",
            "http://example.com/vid~orig.mp4",
            "http://example.com/vid~large.mp4",
            "http://example.com/vid~small.mp4"
        ]

        ranked_large = nasa_images._rank_video_urls(urls, quality='large')
        self.assertTrue(ranked_large[0].endswith("~large.mp4"))

        ranked_small = nasa_images._rank_video_urls(urls, quality='small')
        self.assertTrue(ranked_small[0].endswith("~small.mp4"))

class TestNetworkFunctions(unittest.TestCase):

    @patch('urllib.request.urlopen')
    def test_get_json_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"collection": {"items": []}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = nasa_images.get_json('http://fakeurl.com')
        self.assertIn('collection', result)

    @patch('urllib.request.urlopen')
    def test_get_json_404(self, mock_urlopen):
        err = urllib.error.HTTPError('url', 404, 'Not Found', {}, None)
        mock_urlopen.side_effect = err
        
        result = nasa_images.get_json('http://fakeurl.com')
        self.assertIsNone(result)

    @patch('sys.exit')
    @patch('urllib.request.urlopen')
    def test_get_json_fatal_error(self, mock_urlopen, mock_exit):
        err = urllib.error.HTTPError('url', 500, 'Server Error', {}, None)
        mock_urlopen.side_effect = err
        
        nasa_images.get_json('http://fakeurl.com', fatal=True)
        mock_exit.assert_called_with(1)

    @patch('os.replace')
    @patch('urllib.request.urlopen')
    def test_download_url_success(self, mock_urlopen, mock_replace):
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [b'data chunk', b''] 
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with patch("builtins.open", mock_open()):
            result = nasa_images._download_url("http://fake.url", "/tmp/fake.jpg")
            
        self.assertTrue(result)
        mock_replace.assert_called_once()

class TestItemProcessing(unittest.TestCase):

    @patch('os.path.exists', return_value=True)
    def test_process_image_skip_existing(self, mock_exists):
        item = {
            "data": [{"nasa_id": "img123"}],
            "links": [{"rel": "preview", "href": "http://nasa.gov/image/img~thumb.jpg"}]
        }
        status, url, name, kind = nasa_images._process_image(item, "/tmp")
        self.assertEqual(status, 'sk')

    @patch('nasa_images._download_url', return_value=True)
    @patch('os.path.exists', return_value=False)
    def test_process_image_download_new(self, mock_exists, mock_download_url):
        item = {
            "data": [{"nasa_id": "img123"}],
            "links": [{"rel": "preview", "href": "http://nasa.gov/image/img~thumb.jpg"}]
        }
        status, url, name, kind = nasa_images._process_image(item, "/tmp")
        
        self.assertEqual(status, 'dl')
        self.assertEqual(kind, 'image')
        self.assertTrue(url.endswith("~orig.jpg")) 

    def test_process_image_missing(self):
        item = {"data": [{"nasa_id": "bad123"}], "links": []}
        status, url, name, kind = nasa_images._process_image(item, "/tmp")
        
        self.assertEqual(status, 'missing')
        self.assertIsNone(url)

if __name__ == '__main__':
    with patch('sys.stdout'):
        unittest.main()