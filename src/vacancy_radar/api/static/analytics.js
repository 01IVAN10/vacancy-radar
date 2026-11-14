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

  // Categorical slots 1-3 of a palette checked for colour-blind readers, stepped for
  // each mode: All sources, then each source in slug order.
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const series = dark ? ["#3987e5", "#d95926", "#199e70"] : ["#2a78d6", "#eb6834", "#1baf7a"];
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
            backgroundColor: series[0],
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
    // joining across it, and no number is drawn for it.
    const line = { spanGaps: false, pointRadius: 4, pointHoverRadius: 6, borderWidth: 2 };
    new Chart(canvas, {
      type: "line",
      data: {
        labels: salaries.labels,
        datasets: [
          { ...line, label: "25th percentile", data: salaries.p25, borderColor: band,
            backgroundColor: band, pointRadius: 0, borderWidth: 1 },
          { ...line, label: "75th percentile", data: salaries.p75, borderColor: band,
            backgroundColor: band, pointRadius: 0, borderWidth: 1, fill: "-1" },
          { ...line, label: "Median", data: salaries.median, borderColor: series[0],
            backgroundColor: series[0] },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { filter: (item) => item.text !== "25th percentile" } },
          tooltip: {
            callbacks: {
              label: (item) => `${item.dataset.label}: ${dollars(item.parsed.y)}`,
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
        datasets: jobs.series.map((one, index) => ({
          label: one.name,
          data: one.values,
          borderColor: series[index],
          backgroundColor: series[index],
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

  // A new choice shows at once; without this script, the form's button sends it.
  const choices = document.getElementById("analytics-choices");
  if (choices) {
    choices.addEventListener("change", () => choices.requestSubmit());
  }
})();
