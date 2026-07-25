const statusLine = document.getElementById("status-line");
const gameList = document.getElementById("game-list");
const scanBtn = document.getElementById("scan-btn");
const idleView = document.getElementById("idle-view");
const playView = document.getElementById("play-view");
const gameVideo = document.getElementById("game-video");
const gameAudio = document.getElementById("game-audio");
const fullscreenBtn = document.getElementById("fullscreen-btn");
const endGameBtn = document.getElementById("end-game-btn");
const backgroundGameBanner = document.getElementById("background-game-banner");
const backgroundGameText = document.getElementById("background-game-text");
const backgroundJoinBtn = document.getElementById("background-join-btn");
const backgroundEndGameBtn = document.getElementById("background-end-game-btn");
const storageToggleBtn = document.getElementById("storage-toggle-btn");
const storageForm = document.getElementById("storage-form");
const cifsFields = document.getElementById("cifs-fields");
const cifsHostInput = document.getElementById("cifs-host");
const cifsShareInput = document.getElementById("cifs-share");
const cifsSubpathInput = document.getElementById("cifs-subpath");
const cifsUsernameInput = document.getElementById("cifs-username");
const cifsPasswordInput = document.getElementById("cifs-password");

let whepUrl = null;
//let peerConnection = null; -- 

let videoPeerConnection = null;
let audioPeerConnection = null;

// Same vocabulary as the backend (backend/app/input/keymap.py) — logical
// names that correspond to the input_player1_* binds in RetroArch.
const KEYBOARD_KEY_TO_INPUT = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    Enter: "start",
    ShiftRight: "select",
    KeyX: "a",
    KeyZ: "b",
    KeyS: "x",
    KeyA: "y",
    KeyQ: "l",
    KeyW: "r",
};

// Gamepad API button 0 (physical "A" position on an Xbox pad) -> RetroPad "b",
// and vice-versa — convention used by libretro autoconfigs for Xbox-style pads.
// Not yet validated with a real physical controller; if swapped in real testing,
// flip this mapping here (no architectural changes needed).
const GAMEPAD_BUTTON_TO_KEY = {
    0: "b", 1: "a", 2: "y", 3: "x",
    4: "l", 5: "r",
    8: "select", 9: "start",
    12: "up", 13: "down", 14: "left", 15: "right",
};

let inputSocket = null;
let gamepadPollHandle = null;
const _prevGamepadButtonState = {};

async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || `Error ${response.status}`);
    }
    return data;
}

function renderGames(games) {
    gameList.innerHTML = "";
    if (games.length === 0) {
        gameList.innerHTML = "<li>No games found. Click \"Scan library\".</li>";
        return;
    }
    for (const game of games) {
        const li = document.createElement("li");
        li.className = "game-item";

        const meta = document.createElement("div");
        meta.className = "meta";

        const consoleTag = document.createElement("span");
        consoleTag.className = "console-tag";
        consoleTag.textContent = game.console;

        const titleSpan = document.createElement("span");
        titleSpan.textContent = game.title;

        const btn = document.createElement("button");
        btn.className = "play-btn";
        btn.dataset.id = game.id;
        btn.textContent = "Play";
        btn.addEventListener("click", () => playGame(game.id));

        meta.appendChild(consoleTag);
        meta.appendChild(titleSpan);
        li.appendChild(meta);
        li.appendChild(btn);
        gameList.appendChild(li);
    }
}

async function loadConfig() {
    try {
        const config = await fetchJSON("/config");
        whepUrl = config.whep_url;
        applyStorageConfig(config.storage);
    } catch (err) {
        statusLine.textContent = `Error loading config: ${err.message}`;
    }
}

function applyStorageConfig(storage) {
    if (!storage) {
        return;
    }
    const radio = storageForm.querySelector(`input[name="storage-mode"][value="${storage.mode}"]`);
    if (radio) {
        radio.checked = true;
    }
    cifsFields.classList.toggle("hidden", storage.mode !== "cifs");
    if (storage.mode === "cifs") {
        cifsHostInput.value = storage.cifs_host || "";
        cifsShareInput.value = storage.cifs_share || "";
        cifsSubpathInput.value = storage.cifs_subpath || "";
        cifsUsernameInput.value = storage.cifs_username || "";
    }
}

async function submitStorageForm(event) {
    event.preventDefault();
    const mode = storageForm.querySelector('input[name="storage-mode"]:checked').value;
    statusLine.textContent = "Configuring storage...";
    try {
        const result = mode === "cifs"
            ? await fetchJSON("/storage/cifs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    host: cifsHostInput.value,
                    share: cifsShareInput.value,
                    subpath: cifsSubpathInput.value,
                    username: cifsUsernameInput.value,
                    password: cifsPasswordInput.value,
                }),
            })
            : await fetchJSON("/storage/local", { method: "POST" });
        cifsPasswordInput.value = "";
        statusLine.textContent = `Storage configured: ${result.scanned} game(s) found.`;
        renderGames(result.games);
    } catch (err) {
        statusLine.textContent = `Error configuring storage: ${err.message}`;
    }
}

async function refreshStatus() {
    try {
        const data = await fetchJSON("/status");
        if (data.running && data.game) {
            statusLine.textContent = `Status: playing ${data.game.title}`;
        } else {
            statusLine.textContent = "Status: idle";
        }
        updateBackgroundGameBanner(data);
    } catch (err) {
        statusLine.textContent = `Status: error (${err.message})`;
    }
}

function updateBackgroundGameBanner(data) {
    const isIdleView = !idleView.classList.contains("hidden");
    if (isIdleView && data.running && data.game) {
        backgroundGameText.textContent = `${data.game.title} is running.`;
        backgroundGameBanner.classList.remove("hidden");
    } else {
        backgroundGameBanner.classList.add("hidden");
    }
}

async function joinGame() {
    statusLine.textContent = "Connecting to game in progress...";
    try {
        disconnectWhep();
        showPlayView();
        await connectWhep();
        connectInputSocket();
        await refreshStatus();
    } catch (err) {
        showIdleView();
        statusLine.textContent = `Error connecting: ${err.message}`;
    }
}

async function loadGames() {
    try {
        const games = await fetchJSON("/games");
        renderGames(games);
    } catch (err) {
        statusLine.textContent = `Error loading games: ${err.message}`;
    }
}

async function scanLibrary() {
    scanBtn.disabled = true;
    statusLine.textContent = "Scanning library...";
    try {
        const result = await fetchJSON("/scan", { method: "POST" });
        statusLine.textContent = `Scanned: ${result.scanned} (new: ${result.added}, updated: ${result.updated})`;
        renderGames(result.games);
    } catch (err) {
        statusLine.textContent = `Error scanning: ${err.message}`;
    } finally {
        scanBtn.disabled = false;
    }
}

async function connectWhepTrack(kind, onTrack) {
    const pc = new RTCPeerConnection();

    pc.ontrack = onTrack;
    pc.addTransceiver(kind, { direction: "recvonly" });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const response = await fetch(whepUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: offer.sdp,
    });

    if (!response.ok) {
        pc.close();
        throw new Error(`WHEP ${kind} failed: ${response.status}`);
    }

    const answerSdp = await response.text();
    await pc.setRemoteDescription({
        type: "answer",
        sdp: answerSdp,
    });

    return pc;
}

function showPlayView() {
    idleView.classList.add("hidden");
    playView.classList.remove("hidden");
}

function showIdleView() {
    playView.classList.add("hidden");
    idleView.classList.remove("hidden");
}

async function connectWhep() {
    if (!whepUrl) {
        await loadConfig();
    }

    videoPeerConnection = await connectWhepTrack("video", (event) => {
        gameVideo.srcObject = new MediaStream([event.track]);
    });

    try {
        audioPeerConnection = await connectWhepTrack("audio", (event) => {
            gameAudio.srcObject = new MediaStream([event.track]);

            gameAudio.play().catch((error) => {
                console.warn("Audio autoplay blocked:", error);
            });
        });
    } catch (error) {
        // Audio failure should not prevent the game from running.
        console.warn("Audio connection unavailable:", error);
        audioPeerConnection = null;
    }
}

function disconnectWhep() {
    if (videoPeerConnection) {
        videoPeerConnection.close();
        videoPeerConnection = null;
    }

    if (audioPeerConnection) {
        audioPeerConnection.close();
        audioPeerConnection = null;
    }

    gameVideo.srcObject = null;

    gameAudio.pause();
    gameAudio.srcObject = null;
}

function connectInputSocket() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    inputSocket = new WebSocket(`${proto}//${location.host}/ws/input`);
    inputSocket.addEventListener("open", () => {
        gamepadPollHandle = requestAnimationFrame(pollGamepad);
    });
    inputSocket.addEventListener("close", (event) => {
        stopGamepadPolling();
        // code 4000 = another device took over control (backend closed actively).
        // Return to idle without stopping the game. Any other close is a normal disconnect.
        if (event.code === 4000 && !playView.classList.contains("hidden")) {
            disconnectWhep();
            showIdleView();
            statusLine.textContent = "Control taken over by another device.";
            refreshStatus();
        }
    });
}

function disconnectInputSocket() {
    stopGamepadPolling();
    if (inputSocket) {
        inputSocket.close();
        inputSocket = null;
    }
}

function stopGamepadPolling() {
    if (gamepadPollHandle !== null) {
        cancelAnimationFrame(gamepadPollHandle);
        gamepadPollHandle = null;
    }
}

function pollGamepad() {
    const pads = navigator.getGamepads();
    const pad = pads[0];
    if (pad) {
        for (const indexStr of Object.keys(GAMEPAD_BUTTON_TO_KEY)) {
            const index = Number(indexStr);
            const pressed = pad.buttons[index]?.pressed ?? false;
            if (pressed !== !!_prevGamepadButtonState[index]) {
                _prevGamepadButtonState[index] = pressed;
                sendInputEvent(GAMEPAD_BUTTON_TO_KEY[index], pressed);
            }
        }
    }
    gamepadPollHandle = requestAnimationFrame(pollGamepad);
}

function sendInputEvent(key, pressed) {
    if (inputSocket && inputSocket.readyState === WebSocket.OPEN) {
        inputSocket.send(JSON.stringify({ key, pressed }));
    }
}

function handleGameKeydown(event) {
    if (playView.classList.contains("hidden")) {
        return;
    }
    const key = KEYBOARD_KEY_TO_INPUT[event.code];
    if (key === undefined) {
        return;
    }
    event.preventDefault();
    if (!event.repeat) {
        sendInputEvent(key, true);
    }
}

function handleGameKeyup(event) {
    if (playView.classList.contains("hidden")) {
        return;
    }
    const key = KEYBOARD_KEY_TO_INPUT[event.code];
    if (key === undefined) {
        return;
    }
    event.preventDefault();
    sendInputEvent(key, false);
}

async function playGame(gameId) {
    statusLine.textContent = "Starting game...";
    try {
        await fetchJSON("/play", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ game_id: gameId }),
        });
        showPlayView();
        await connectWhep();
        connectInputSocket();
        await refreshStatus();
    } catch (err) {
        statusLine.textContent = `Error starting game: ${err.message}`;
    }
}

async function stopGame() {
    statusLine.textContent = "Stopping game...";
    try {
        await fetchJSON("/stop", { method: "POST" });
        disconnectInputSocket();
        disconnectWhep();
        showIdleView();
        await refreshStatus();
        await loadGames();
    } catch (err) {
        statusLine.textContent = `Error stopping game: ${err.message}`;
    }
}

scanBtn.addEventListener("click", scanLibrary);
endGameBtn.addEventListener("click", stopGame);
backgroundJoinBtn.addEventListener("click", joinGame);
backgroundEndGameBtn.addEventListener("click", stopGame);
storageToggleBtn.addEventListener("click", () => storageForm.classList.toggle("hidden"));
storageForm.addEventListener("submit", submitStorageForm);
for (const radio of storageForm.querySelectorAll('input[name="storage-mode"]')) {
    radio.addEventListener("change", () => {
        cifsFields.classList.toggle("hidden", radio.value !== "cifs" || !radio.checked);
    });
}
fullscreenBtn.addEventListener("click", () => {
    gameVideo.requestFullscreen();
});
document.addEventListener("keydown", handleGameKeydown);
document.addEventListener("keyup", handleGameKeyup);

// This is real-time streaming, there is no "pause the game" — any pause attempt
// (video click, media key from keyboard/OS, system gesture)
// is reverted immediately, before the user mistakes it for the game
// having actually stopped on the Pi.
gameVideo.addEventListener("pause", () => {
    if (!playView.classList.contains("hidden")) {
        gameVideo.play().catch(() => {});
    }
});
gameVideo.addEventListener("click", (event) => event.preventDefault());
gameVideo.addEventListener("dblclick", (event) => event.preventDefault());

loadConfig();
loadGames();
refreshStatus();
setInterval(refreshStatus, 5000);
