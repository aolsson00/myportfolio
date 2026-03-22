# R Shiny apps

## COVID-19 Map

Folder: [`COVID-19 Map/`](COVID-19%20Map/)

Interactive **Shiny** dashboard of provisional **COVID-19** (and related) death counts by state, using CDC data (`Provisional_COVID-19_Death_Counts_by_Week_Ending_Date_and_State_20240426.csv`) and a **US map** built with **echarts4r** (GeoJSON from the `topogram` US states data).

**Run locally**

1. Install R and the packages used in `app.R` (e.g. `shiny`, `shinythemes`, `dplyr`, `echarts4r`, `lubridate`, `tidyr`, `shinydashboard`, `shinyWidgets`).
2. Set the working directory to `COVID-19 Map/` so the CSV loads correctly.
3. In R: `shiny::runApp("app.R")` or open `app.R` in RStudio and click **Run App**.

A deployment record for **shinyapps.io** is under `rsconnect/` for the published app URL shown in the `.dcf` file.

---

Add new apps as additional subfolders here and list them in this file.
