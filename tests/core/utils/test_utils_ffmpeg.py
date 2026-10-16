import shutil
import subprocess
from types import SimpleNamespace

import pytest
from mutagen.id3 import ID3

from tiddl.core.metadata.track import Metadata, add_mp3_metadata
from tiddl.core.utils.ffmpeg import convert_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture
def flac(tmp_path):
    path = tmp_path / "Mr. Brightside.flac"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=f=440:d=1:sample_rate=96000", "-c:a", "flac", str(path)],
        check=True,
    )
    return path


@pytest.mark.parametrize("fmt", ["mp3", "flac"])
def test_convert_audio(flac, fmt):
    out = convert_audio(flac, fmt)
    assert out.name == f"Mr. Brightside.{fmt}"
    assert out.exists()
    if fmt == "mp3":
        assert not flac.exists()


def test_mp3_metadata(flac):
    mp3 = convert_audio(flac, "mp3")
    credit = SimpleNamespace(type="Producer", contributors=[SimpleNamespace(name="P")])
    add_mp3_metadata(mp3, Metadata(
        title="Mr. Brightside", track_number="2", disc_number="1",
        copyright="(C) 2004", album_artist="The Killers", artists=["The Killers"],
        album_title="Hot Fuss", date="2004-06-07T00:00:00", isrc="USIR20400274",
        genre="Rock", cover_data=b"\xff\xd8\xff", credits=[credit],
    ))
    tags = ID3(mp3)
    assert str(tags["TIT2"]) == "Mr. Brightside"
    assert str(tags["TALB"]) == "Hot Fuss"
    assert str(tags["TDRC"]) == "2004-06-07"
    assert str(tags["TSRC"]) == "USIR20400274"
    assert tags.getall("APIC")
    assert str(tags["TXXX:PRODUCER"]) == "P"
