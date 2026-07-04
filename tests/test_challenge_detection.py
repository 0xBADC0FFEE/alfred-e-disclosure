"""Challenge fixture is classified as a challenge; a normal listing is not."""
import list_reports


def test_challenge_fixture_is_challenge(challenge_html):
    assert list_reports._is_challenge_page(challenge_html) is True


def test_spinner_challenge_fixture_is_challenge(spinner_challenge_html):
    # The classic ~2 KB ServicePipe spinner interstitial the portal actually
    # serves for SBER: no rotate-CAPTCHA text, but a js-challenge-loader mount.
    assert list_reports._is_challenge_page(spinner_challenge_html) is True


def test_normal_files_page_is_valid(normal_html):
    assert list_reports._is_challenge_page(normal_html) is False


def test_empty_body_is_challenge():
    assert list_reports._is_challenge_page("") is True


def test_large_non_files_page_without_robo_markers_is_not_challenge():
    # New heuristic ignores size: a big page lacking robo-check markers is not
    # treated as a challenge just for being unrecognised.
    assert list_reports._is_challenge_page("<html>" + "x" * 20000 + "</html>") is False
