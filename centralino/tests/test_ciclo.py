"""stato_git del ciclo di vita delle sessioni delegate (t-20261029-lbgswf, B), su repo veri."""

import subprocess

from centralino.ciclo import stato_git


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(d), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


def test_stato_git(tmp_path):
    remoto = tmp_path / "remoto.git"
    _git(tmp_path, "init", "-q", "--bare", str(remoto))
    ws = tmp_path / "ws"
    _git(tmp_path, "clone", "-q", str(remoto), str(ws))
    (ws / "a.txt").write_text("a")
    _git(ws, "add", "a.txt")
    _git(ws, "commit", "-q", "-m", "a")
    assert stato_git(str(ws)) == (False, "1 commit non pushati")
    _git(ws, "push", "-q", "origin", "HEAD")
    assert stato_git(str(ws)) == (True, "pulita e pushata")
    (ws / "b.txt").write_text("b")
    assert stato_git(str(ws)) == (False, "1 file non committati")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert stato_git(str(plain)) == (True, "non è un repo git")
    assert stato_git(str(tmp_path / "manca"))[0] is False
