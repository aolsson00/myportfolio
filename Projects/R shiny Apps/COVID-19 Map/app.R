# Load R packages
library(shiny)
library(shinythemes)
library(dplyr)
library(echarts4r)
library(lubridate)
library(tidyr)


# Import and tidy data
#https://data.cdc.gov/NCHS/Provisional-COVID-19-Death-Counts-by-Week-Ending-D/r8kw-7aab/about_data

covid_data <- read.csv("/Users/olssons/Documents/BIFX551/Final Project/Provisional_COVID-19_Death_Counts_by_Week_Ending_Date_and_State_20240426.csv")

deaths_by_year_state <- covid_data %>% 
  group_by(State, Year, Month) %>%
  summarise_at(c("COVID.19.Deaths", "Pneumonia.Deaths", "Influenza.Deaths"), sum, na.rm=TRUE) %>%
  filter(State != "United States") %>%
  rename("SARS-CoV-2" = COVID.19.Deaths, 
         Pneumonia = Pneumonia.Deaths,
         Influenza = Influenza.Deaths)

deaths_by_year_state <- na.omit(deaths_by_year_state)

deaths_by_year_state <- deaths_by_year_state %>%
  pivot_longer(cols = c("SARS-CoV-2", "Pneumonia", "Influenza"), names_to = "Illness", values_to = "total_deaths")


plot_map <- function(source_data) {
  json <- jsonlite::read_json("https://raw.githubusercontent.com/shawnbot/topogram/master/data/us-states.geojson")
  
  ## remove alaska and hawaii
  json$features <- Filter(function(x) !x$properties$name %in% c("Alaska", "Hawaii"), json$features)
  
  source_data %>% 
    echarts4r::e_charts(x = State) %>% 
    echarts4r::e_map_register("USA", json) %>% 
    echarts4r::e_map(total_deaths, map = "USA") %>% 
    echarts4r::e_visual_map(
      inRange = list(color = c("#FED2D2", "#7a0000")),
      min = min(source_data$total_deaths, na.rm = TRUE),
      max = max(source_data$total_deaths, na.rm = TRUE)
    )
}


# Define UI
ui <- fluidPage(
  #Setting the theme
  theme = shinytheme("cosmo"),
  #Setting the title
  titlePanel("SARS-CoV-2 -- Final Project for BIFX551"),

  # Sidebar with a selection input for variable by days
  sidebarLayout(
    sidebarPanel(
      selectInput('Year', 'Select Year', unique(deaths_by_year_state$Year)),
      selectInput('Month', 'Select Month', unique(deaths_by_year_state$Month)),
      checkboxGroupInput(
        "Illness", "Filter by Illness",
        choices = c("SARS-CoV-2", "Pneumonia", "Influenza"), 
        selected = c("SARS-CoV-2", "Pneumonia", "Influenza")),
      ),
    mainPanel(
      echarts4rOutput("plot_map")
    )
  )
)

# Define server
server <- function(input, output, session) {
  
  
  output$plot_map <- renderEcharts4r({
    req(input$Illness)
    filtered_data <- deaths_by_year_state %>%
      filter(Year == input$Year & Month == input$Month & Illness == input$Illness)
    
    plot_map(filtered_data)
  })
}


# Run the application 
shinyApp(ui = ui, server = server)


