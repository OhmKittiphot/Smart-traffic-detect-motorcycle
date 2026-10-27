"use strict";

// The live view's video. It is received over WebRTC (dashboard/webrtc.py) into <video id="stream-video">, which
// needs less bandwidth and has less delay than the MJPEG stream of <img id="stream">. The MJPEG stream shows
// until the WebRTC video plays, and stays as the fallback when WebRTC cannot connect. Exposed as
// window.LiveVideo for dashboard.js while the live view is shown; leaving it closes the connection.
window.Dashboard.onPage("#stream-video", (page) => {
  const CONNECT_TIMEOUT_MS = 8000;   // no picture by then: fall back to MJPEG
  const GATHER_TIMEOUT_MS = 2000;    // the offer goes out with the addresses found by then
  const img = document.getElementById("stream");
  const video = document.getElementById("stream-video");
  let pc = null;
  let timer = null;
  let played = false;   // WebRTC has worked on this page
  // Set when WebRTC never got a picture: the MJPEG stream is used until the page is opened again
  let mjpegOnly = !window.RTCPeerConnection;

  function showMjpeg() {
    video.hidden = true;
    img.hidden = false;
    img.src = `/stream/?t=${Date.now()}`;
  }

  // The first frame arrived: the video takes the MJPEG stream's place
  video.addEventListener("playing", () => {
    clearTimeout(timer);
    played = true;
    video.hidden = false;
    img.hidden = true;
    img.removeAttribute("src");
  });

  function stop() {
    clearTimeout(timer);
    pc?.close();
    pc = null;
    video.srcObject = null;
    img.removeAttribute("src");
  }

  function fallBack() {
    stop();
    mjpegOnly = !played;
    showMjpeg();
  }

  function gathered(peer) {
    return new Promise((resolve) => {
      if (peer.iceGatheringState === "complete") return resolve();
      peer.addEventListener("icegatheringstatechange", () => {
        if (peer.iceGatheringState === "complete") resolve();
      });
      setTimeout(resolve, GATHER_TIMEOUT_MS);
    });
  }

  async function connect(peer) {
    peer.addTransceiver("video", { direction: "recvonly" });
    peer.addEventListener("track", (ev) => { video.srcObject = new MediaStream([ev.track]); });
    peer.addEventListener("connectionstatechange", () => {
      if (peer === pc && peer.connectionState === "failed") fallBack();
    });
    await peer.setLocalDescription(await peer.createOffer());
    await gathered(peer);
    const answer = await window.Dashboard.api("/api/webrtc/offer/",
                                              { method: "POST", body: JSON.stringify({ sdp: peer.localDescription.sdp }) });
    await peer.setRemoteDescription(answer);
  }

  // (Re)open the video: on load, when the tab is shown again, and after the server was lost
  function reload() {
    stop();
    if (document.hidden) return;
    showMjpeg();   // shown until the new connection plays
    if (mjpegOnly) return;
    const peer = new RTCPeerConnection();
    pc = peer;
    timer = setTimeout(fallBack, CONNECT_TIMEOUT_MS);
    // A connection closed by a later reload() fails too; only the current one falls back
    connect(peer).catch(() => { if (peer === pc) fallBack(); });
  }

  reload();
  page.onLeave(stop);
  window.LiveVideo = { reload, stop };
});
