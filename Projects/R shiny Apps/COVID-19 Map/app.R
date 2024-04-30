# Load Rpackages
library(shiny)
library(shinythemes)
library(dplyr)
library(echarts4r)
library(lubridate)
library(tidyr)
library(shinydashboard)
library(shinyWidgets)

# Import and tidy data
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

# deaths_by_year_state$Month <- sprintf("%02d", deaths_by_year_state$Month)

# Create a new column by combining year and month
deaths_by_year_state <- deaths_by_year_state %>%
  mutate(Year_Month = paste(Year, Month, sep = "."))

# Define UI
ui <- fluidPage(
  theme = shinytheme("cosmo"),
  titlePanel("SARS-CoV-2 -- Final Project for BIFX551"),
  sidebarLayout(
    sidebarPanel(
      sliderTextInput("year_month_slider",
                      label="Select Date:",
                      choices = unique(deaths_by_year_state$Year_Month),
                      selected = unique(deaths_by_year_state$Year_Month)[1],
                      animate = animationOptions(interval = 1200, loop = FALSE)),
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
  # Reactive expression to get the current value of the slider
  current_year_month <- reactive({
    selected_text <- input$year_month_slider
    selected_index <- which(unique(deaths_by_year_state$Year_Month) == selected_text)
    selected_year_month <- unique(deaths_by_year_state$Year_Month)[selected_index]
    return(selected_year_month)
  })
  
  
  output$plot_map <- renderEcharts4r({
    req(input$Illness)
    
    # Get the selected Year_Month value based on the index selected by the slider
    selected_year_month <- current_year_month()
    
    # Filter the data based on the selected Year_Month and Illness
    filtered_data <- deaths_by_year_state %>%
      filter(Year_Month == selected_year_month & Illness %in% input$Illness)
    
    plot_map(filtered_data)
  })
}


# Run the application 
shinyApp(ui = ui, server = server)

