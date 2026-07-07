import unittest
from unittest.mock import MagicMock, patch
from app.services.player import PlayerService, StreamNotReadyError  # Adjust import based on exact repository structure

class TestPlayerService(unittest.TestCase):

    def setUp(self):
        # Create mock dependencies for constructor injection
        self.mock_driver = MagicMock()
        self.mock_streaming = MagicMock()
        self.mock_input = MagicMock()
        
        # Inject mocks into the player service
        self.service = PlayerService(
            driver=self.mock_driver, 
            streaming=self.mock_streaming, 
            input_provider=self.mock_input
        )

    @patch("app.services.player.PlayerService._is_stream_ready", return_value=True)
    def test_play_stop_orchestration(self, mock_ready):
        """Test complete play and stop life cycle orchestration."""
        mock_game = MagicMock()
        self.service.play(mock_game)
        self.service.stop()
        self.mock_driver.stop.assert_called_once()
    @patch("time.sleep", return_value=None)  # Fast-forward sleep intervals during tests
    def test_wait_stream_ready_timeout_path(self, mock_sleep):
        """Validate that _wait_stream_ready throws or handles timeouts safely if stream isn't ready."""
        self.mock_streaming.is_ready.return_value = False
        
        # Assuming it returns False or raises a TimeoutError on failure paths
        with self.assertRaises(StreamNotReadyError):
            self.service._wait_stream_ready()
    def test_status_when_process_dies(self):
        """Verify service accurately reports state changes when the underlying driver process dies."""
        self.mock_driver.status.return_value = False

        running, game = self.service.status()
        self.assertFalse(running)

if __name__ == "__main__":
    unittest.main()
