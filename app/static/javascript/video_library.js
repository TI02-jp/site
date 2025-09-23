(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const videoItems = document.querySelectorAll("[data-video-item]");
        const playerFrame = document.querySelector("[data-video-player]");
        const titleTarget = document.querySelector("[data-video-title]");
        const descriptionTarget = document.querySelector("[data-video-description]");

        if (!videoItems.length || !playerFrame) {
            return;
        }

        const setActiveVideo = (item) => {
            const embedUrl = item.getAttribute("data-video-url");
            if (embedUrl && playerFrame.src !== embedUrl) {
                playerFrame.src = embedUrl;
            }

            videoItems.forEach((node) => node.classList.remove("active"));
            item.classList.add("active");

            if (titleTarget) {
                titleTarget.textContent = item.getAttribute("data-video-name") || "Vídeo";
            }

            if (descriptionTarget) {
                const description = item.getAttribute("data-video-description") || "";
                descriptionTarget.textContent = description;
                if (description.trim()) {
                    descriptionTarget.classList.remove("d-none");
                } else {
                    descriptionTarget.classList.add("d-none");
                }
            }
        };

        videoItems.forEach((item) => {
            item.addEventListener("click", function (event) {
                event.preventDefault();
                setActiveVideo(item);
            });

            item.addEventListener("keypress", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setActiveVideo(item);
                }
            });
        });
    });
})();
