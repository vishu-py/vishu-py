# How this profile works

Everything you see in `README.md` is drawn by `scripts/build.py` from two data files, and a GitHub Action redraws it every 8 hours with live numbers.

| Path | What it is |
| --- | --- |
| `data/profile.json` | All the words: hero rows, taglines, projects, hackathons, DSA topics, learning tracks, skills. Edit this, not the SVGs. |
| `data/portrait.json` | The three photo layers the hero animates (colour cut-out, edge map, silhouette). Made once by `scripts/portrait.py`. |
| `data/leetcode-cache.json` | Last good LeetCode numbers. Used if LeetCode blocks a request, so the card never shows made-up numbers. |
| `scripts/build.py` | Draws every SVG in `assets/` and fills the skills and projects blocks of the README. Standard library only. |
| `scripts/portrait.py` | Cuts the photo out, cleans the hair edge, enhances it and writes the hero layers and avatar images. Run locally only. |
| `scripts/preview.py` | Renders `preview.html` so you can check the README locally. |
| `.github/workflows/profile.yml` | Runs `build.py`, draws the contribution snake, commits the result. |

## Publish it

1. On GitHub, create a **public** repository named exactly **`vishu-py`** (same as your username). GitHub shows its README on your profile.
2. Push this folder to it:

   ```powershell
   git init -b main
   git add .
   git commit -m "Profile README"
   git remote add origin https://github.com/vishu-py/vishu-py.git
   git push -u origin main
   ```

3. Open the repository's **Actions** tab, choose **refresh profile**, and click **Run workflow**. The first run draws the contribution snake (until then a placeholder shows).
4. Profile picture: GitHub → Settings → Public profile → upload one of `profile-picture/avatar-studio-ring.jpg`, `avatar-studio.jpg`, `avatar-aurora.jpg` or `avatar-light.jpg`. (These files are not pushed; they stay on your computer.)
5. Suggested bio: `AI undergrad @ SVNIT Surat · DSA in C++ · full-stack + AI/ML · hackathons`
6. Pin your own repositories first: TaskFlow, leetcode-solutions-cpp, Hindi-Text-Restoration, YouTube-clone, then VAANI and PARAKH.

## Change something

- Words, projects, statuses: edit `data/profile.json`, then `python scripts/build.py` (or just push; the Action runs on changes to `data/` and `scripts/`).
- New photo: put it at `source/photo.jpg`, delete `source/mask.png`, then
  `pip install pillow numpy opencv-python pymatting rembg` and `python scripts/portrait.py source/photo.jpg`.
- Preview locally: `python scripts/preview.py`, then `python -m http.server 8765` and open `http://127.0.0.1:8765/preview.html`.
