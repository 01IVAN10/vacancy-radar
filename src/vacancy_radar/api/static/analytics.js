// Draws the charts of /analytics from the page's JSON block (#analytics-data).
// The tables under the charts hold the same numbers, so the page reads without
// this script. Loaded with `defer`, after Chart.js.
"use strict";

(function () {
  const dataElement = document.getElementById("analytics-data");
  if (!dataElement || typeof Chart === "undefined") {
    return;
  }
  const data = JSON.parse(dataElement.textContent);

  // Slots 1-3 of a categorical palette checked for colour-blind readers, stepped for
  // each mode. A series keeps its colour by its key, so a new source never repaints
  // the others; a source without a colour here is drawn grey until it gets one.
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const colours = dark
    ? { total: "#3987e5", djinni: "#d95926", dou: "#199e70" }
    : { total: "#2a78d6", djinni: "#eb6834", dou: "#1baf7a" };
  const otherColour = dark ? "#a3a29b" : "#75746e";
  const colourOf = (key) => colours[key] || otherColour;
  const band = dark ? "rgba(57, 135, 229, 0.25)" : "rgba(42, 120, 214, 0.18)";

  const styles = getComputedStyle(document.body);
  Chart.defaults.color = styles.getPropertyValue("--pico-muted-color").trim() || "#666";
  Chart.defaults.borderColor =
    styles.getPropertyValue("--pico-muted-border-color").trim() || "#ddd";
  Chart.defaults.font.family = styles.fontFamily;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = false;

  const dollars = (value) => "$" + value.toLocaleString("en-US");

  function skillsChart(canvas) {
    const skills = data.skills;
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: skills.labels,
        datasets: [
          {
            label: "Share of jobs, %",
            data: skills.shares,
            backgroundColor: colours.total,
            borderRadius: 4,
            maxBarThickness: 18,
          },
        ],
      },
      options: {
        indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) =>
                `${skills.n[item.dataIndex]} of ${skills.jobs} jobs (${item.parsed.x}%)`,
            },
          },
        },
        scales: {
          x: { beginAtZero: true, ticks: { callback: (value) => value + "%" } },
          y: { grid: { display: false } },
        },
      },
    });
  }

  function salariesChart(canvas) {
    const salaries = data.salaries;
    // A hidden week is null in every series: the lines break there instead of
    // joining across it, and no number is drawn for it. `tooltipLabel` names a
    // series in the tooltip; the legend names the band once, by its p75 edge.
    const line = { spanGaps: false, pointRadius: 4, pointHoverRadius: 6, borderWidth: 2 };
    new Chart(canvas, {
      type: "line",
      data: {
        labels: salaries.labels,
        datasets: [
          { ...line, label: "25th percentile", tooltipLabel: "25th percentile",
            data: salaries.p25, borderColor: band, backgroundColor: band, pointRadius: 0,
            borderWidth: 1 },
          { ...line, label: "25th–75th percentile", tooltipLabel: "75th percentile",
            data: salaries.p75, borderColor: band, backgroundColor: band, pointRadius: 0,
            borderWidth: 1, fill: "-1" },
          { ...line, label: "Median", tooltipLabel: "Median", data: salaries.median,
            borderColor: colours.total, backgroundColor: colours.total },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { filter: (item) => item.text !== "25th percentile" } },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.tooltipLabel}: ${dollars(item.parsed.y)}`,
              footer: (items) => `n = ${salaries.n[items[0].dataIndex]}`,
            },
          },
        },
        scales: {
          y: { beginAtZero: true, ticks: { callback: dollars } },
          x: { grid: { display: false } },
        },
      },
    });
  }

  function jobsChart(canvas) {
    const jobs = data.jobs;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: jobs.labels,
        datasets: jobs.series.map((one) => ({
          label: one.name,
          data: one.values,
          borderColor: colourOf(one.key),
          backgroundColor: colourOf(one.key),
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
        })),
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: { grid: { display: false } },
        },
      },
    });
  }

  const charts = {
    "skills-chart": skillsChart,
    "salaries-chart": salariesChart,
    "jobs-chart": jobsChart,
  };
  for (const [id, draw] of Object.entries(charts)) {
    const canvas = document.getElementById(id);
    if (canvas) {
      draw(canvas);
    }
  }
})();
