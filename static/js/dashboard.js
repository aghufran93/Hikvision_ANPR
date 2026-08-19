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
   EXPORT REPORT

   Server-side rendered xlsx/pdf with embedded plate thumbnails
   (/api/export), filtered by the same search/direction/lpr controls
   used for the live table plus a date range and vehicle/plate type
   picked here - those two aren't in the live filter bar since the
   table only ever shows the latest 500 events, not a date-bounded
   set.
========================================================= */

async function loadExportFilterOptions() {

    try {

        const response =
            await fetch(
                "/api/filters",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();

        fillOptions(
            "export-vehicle-type",
            data.vehicle_types || []
        );

        fillOptions(
            "export-plate-type",
            data.plate_types || []
        );

    }

    catch (error) {
        console.error(
            "Failed to load export filter options:",
            error
        );
    }
}


function fillOptions(selectId, values) {

    const select =
        document.getElementById(selectId);

    values.forEach(value => {

        const option =
            document.createElement("option");

        option.value = value;
        option.textContent = value;

        select.appendChild(option);
    });
}


function buildExportParams() {

    const params = new URLSearchParams();

    const search =
        document
        .getElementById("search")
        .value
        .trim();

    const direction =
        document
        .getElementById("direction-filter")
        .value;

    const lprFilter =
        document
        .getElementById("lpr-filter")
        .value;

    const startDate =
        document
        .getElementById("export-start-date")
        .value;

    const endDate =
        document
        .getElementById("export-end-date")
        .value;

    const vehicleType =
        document
        .getElementById("export-vehicle-type")
        .value;

    const plateType =
        document
        .getElementById("export-plate-type")
        .value;

    if (search) params.set("search", search);
    if (direction) params.set("direction", direction);
    if (lprFilter) params.set("lpr", lprFilter);
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    if (vehicleType) params.set("vehicle_type", vehicleType);
    if (plateType) params.set("plate_type", plateType);

    return params;
}


function exportErrorMessage(status) {

    if (status === 404) {
        return "No events match the selected filters.";
    }

    if (status === 413) {
        return (
            "Too many events matched (max 2000) - " +
            "narrow the date range or filters and try again."
        );
    }

    return "Export failed (error " + status + "). Please try again.";
}


async function exportReport(format, button) {

    const params = buildExportParams();
    params.set("format", format);

    button.disabled = true;

    const originalLabel = button.textContent;
    button.textContent = "Exporting...";

    try {

        const response =
            await fetch(
                "/api/export?" + params.toString()
            );

        if (!response.ok) {
            alert(exportErrorMessage(response.status));
            return;
        }

        const blob = await response.blob();

        const disposition =
            response.headers.get("Content-Disposition") || "";

        const match =
            disposition.match(/filename="?([^"]+)"?/);

        const filename =
            match
                ? match[1]
                : "anpr_export." + format;

        const link = document.createElement("a");

        link.href = URL.createObjectURL(blob);
        link.download = filename;

        document.body.appendChild(link);
        link.click();
        link.remove();

        URL.revokeObjectURL(link.href);

    }

    catch (error) {

        console.error(
            "Export failed:",
            error
        );

        alert("Export failed. Please check your connection and try again.");

    }

    finally {

        button.disabled = false;
        button.textContent = originalLabel;
    }
}


document
.getElementById("export-toggle")
.addEventListener(
    "click",
    function() {

        const panel =
            document.getElementById("export-panel");

        panel.classList.toggle("open");

        this.classList.toggle(
            "active",
            panel.classList.contains("open")
        );
    }
);

document
.getElementById("export-xlsx")
.addEventListener(
    "click",
    function() {
        exportReport("xlsx", this);
    }
);

document
.getElementById("export-pdf")
.addEventListener(
    "click",
    function() {
        exportReport("pdf", this);
    }
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
loadExportFilterOptions();
connectEventStream();

setInterval(
    loadEvents,
    POLL_INTERVAL_MS
);
