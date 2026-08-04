let allEvents = [];


/* =========================================================
   IMAGE URL
========================================================= */

function imageUrl(event, filename) {

    if (!filename) {
        return null;
    }

    return (
        "/images/" +
        event.direction +
        "/" +
        encodeURIComponent(filename)
    );
}


/* =========================================================
   LATEST EVENT CARD
========================================================= */

function renderLatest(elementId, event) {

    const container =
        document.getElementById(elementId);

    if (!event) {
        return;
    }

    const image =
        imageUrl(
            event,
            event.vehicle_image || event.plate_image
        );

    const plateClass =
        event.lpr_read
            ? ""
            : "not-read";

    container.querySelector(
        ".latest-content"
    ).innerHTML = `

        <div class="latest-image">

            ${
                image
                ? `
                    <img
                        src="${image}"
                        onclick="openModal('${image}')"
                    >
                  `
                : `
                    <div class="no-image">
                        No Image
                    </div>
                  `
            }

        </div>

        <div class="latest-info">

            <div class="latest-plate ${plateClass}">
                ${escapeHtml(event.full_plate)}
            </div>

            <div class="latest-meta">

                <div>
                    State
                    <strong>
                        ${escapeHtml(event.state)}
                    </strong>
                </div>

                <div>
                    Confidence
                    <strong>
                        ${escapeHtml(event.confidence_display)}
                    </strong>
                </div>

                <div>
                    Vehicle
                    <strong>
                        ${escapeHtml(event.vehicle_type)}
                    </strong>
                </div>

                <div>
                    Time
                    <strong>
                        ${escapeHtml(event.time)}
                    </strong>
                </div>

                <div>
                    Colour
                    <strong>
                        ${escapeHtml(event.vehicle_color)}
                    </strong>
                </div>

                <div>
                    Date
                    <strong>
                        ${escapeHtml(event.date)}
                    </strong>
                </div>

            </div>

        </div>
    `;
}


/* =========================================================
   HISTORY
========================================================= */

function renderTable() {

    const search =
        document
        .getElementById("search")
        .value
        .trim()
        .toLowerCase();

    const direction =
        document
        .getElementById("direction-filter")
        .value;

    const lprFilter =
        document
        .getElementById("lpr-filter")
        .value;

    const body =
        document.getElementById(
            "events-body"
        );

    body.innerHTML = "";

    const filtered =
        allEvents.filter(event => {

            if (
                search &&
                !event.full_plate
                    .toLowerCase()
                    .includes(search)
            ) {
                return false;
            }

            if (
                direction &&
                event.direction !== direction
            ) {
                return false;
            }

            if (
                lprFilter === "read" &&
                !event.lpr_read
            ) {
                return false;
            }

            if (
                lprFilter === "not-read" &&
                event.lpr_read
            ) {
                return false;
            }

            return true;
        });


    filtered.forEach(event => {

        const row =
            document.createElement("tr");

        const plateImage =
            imageUrl(
                event,
                event.plate_image
            );

        const fullImage =
            imageUrl(
                event,
                event.full_image
            );

        const badgeClass =
            event.direction === "Entry"
                ? "badge-entry"
                : "badge-exit";

        const plateClass =
            event.lpr_read
                ? ""
                : "not-read";

        row.innerHTML = `

            <td>
                <strong>
                    ${escapeHtml(event.time)}
                </strong>
                <br>
                <small>
                    ${escapeHtml(event.date)}
                </small>
            </td>


            <td>

                <span class="badge ${badgeClass}">
                    ${escapeHtml(event.direction)}
                </span>

            </td>


            <td>

                ${
                    plateImage
                    ? `
                        <img
                            class="plate-thumb"
                            src="${plateImage}"
                            onclick="openModal('${plateImage}')"
                        >
                      `
                    : `
                        <span>
                            N/A
                        </span>
                      `
                }

            </td>


            <td>

                <span class="plate-text ${plateClass}">
                    ${escapeHtml(event.full_plate)}
                </span>

            </td>


            <td>
                ${escapeHtml(event.state)}
            </td>


            <td>
                ${escapeHtml(event.vehicle_type)}
            </td>


            <td class="confidence">
                ${escapeHtml(event.confidence_display)}
            </td>


            <td>

                ${
                    fullImage
                    ? `
                        <button
                            class="evidence-button"
                            onclick="openModal('${fullImage}')"
                        >
                            View
                        </button>
                      `
                    : "-"
                }

            </td>
        `;

        body.appendChild(row);
    });
}


/* =========================================================
   LOAD API
========================================================= */

async function loadEvents() {

    try {

        const response =
            await fetch(
                "/api/events",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            throw new Error(
                "API error " +
                response.status
            );
        }

        const data =
            await response.json();

        allEvents =
            data.events || [];

        renderLatest(
            "latest-entry",
            data.latest_entry
        );

        renderLatest(
            "latest-exit",
            data.latest_exit
        );

        renderTable();

        document.getElementById(
            "stat-total"
        ).textContent =
            data.stats.total;

        document.getElementById(
            "stat-entry"
        ).textContent =
            data.stats.entry;

        document.getElementById(
            "stat-exit"
        ).textContent =
            data.stats.exit;

        document.getElementById(
            "stat-rate"
        ).textContent =
            data.stats.read_rate + "%";

        document.getElementById(
            "last-update"
        ).textContent =
            new Date()
            .toLocaleTimeString();

    }

    catch (error) {

        console.error(
            "Dashboard refresh failed:",
            error
        );
    }
}


/* =========================================================
   MODAL
========================================================= */

function openModal(url) {

    const modal =
        document.getElementById(
            "image-modal"
        );

    const image =
        document.getElementById(
            "modal-image"
        );

    image.src = url;

    modal.style.display =
        "flex";
}


function closeModal() {

    document.getElementById(
        "image-modal"
    ).style.display =
        "none";
}


document.getElementById(
    "image-modal"
).addEventListener(
    "click",
    function(event) {

        if (event.target === this) {
            closeModal();
        }

    }
);


/* =========================================================
   SECURITY
========================================================= */

function escapeHtml(value) {

    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


/* =========================================================
   FILTER EVENTS
========================================================= */

document
.getElementById("search")
.addEventListener(
    "input",
    renderTable
);

document
.getElementById("direction-filter")
.addEventListener(
    "change",
    renderTable
);

document
.getElementById("lpr-filter")
.addEventListener(
    "change",
    renderTable
);


/* =========================================================
   LIVE UPDATES

   Server-Sent Events push a lightweight "something changed" signal
   the instant the backend index updates (new event, or a late image
   attached to an existing one), so the dashboard reacts immediately
   instead of waiting for its next poll tick. Polling still runs
   underneath as a safety net - covers the case where SSE is blocked
   by a proxy, or the connection drops and hasn't reconnected yet.
   Interval is configurable (DASHBOARD_POLL_INTERVAL_MS in .env,
   default 250ms); it's cheap even at that rate because /api/events
   is served from an in-memory index, not re-read from disk.
========================================================= */

const POLL_INTERVAL_MS =
    window.DASHBOARD_POLL_INTERVAL_MS || 250;

function connectEventStream() {

    if (!window.EventSource) {
        return;
    }

    const source =
        new EventSource("/api/events/stream");

    source.addEventListener(
        "update",
        loadEvents
    );

    source.onerror = function() {
        // EventSource retries the connection on its own (see the
        // "retry:" field sent by the server); the polling interval
        // below keeps the dashboard current in the meantime.
    };
}


/* =========================================================
   START
========================================================= */

loadEvents();
connectEventStream();

setInterval(
    loadEvents,
    POLL_INTERVAL_MS
);
