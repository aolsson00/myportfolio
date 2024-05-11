</head>
<body>
    <header>
        <h1>Welcome to Flight Booking</h1>
    </header>
    <nav>
<a href="index.html">Home</a>
<div style="width: 20px;"></div>
<a href="create_user_form.html">Create New User</a>
<div style="width: 20px;"></div>
<a href="search_flights_form.html">Flights Search</a>
<div style="width: 20px;"></div>
<a href="input_ticket_form.html">Make Reservation</a>
<div style="width: 20px;"></div>
<a href="create_passenger_form.html">Create Passenger</a>
<div style="width: 20px;"></div>
<a href="input_address_form.html">Passenger Address</a>
<div style="width: 20px;"></div>
<a href="about_crew_form.php">About Crew</a>
<div style="width: 20px;"></div>

<?php 
include_once 'db.php'; 
include 'display.php'; 

echo "<h2>Current Flight Information: </h2>"; 
display("SELECT * FROM flight;"); 
?> 

<br/> 
<form action="display_crew.php" method="post"> 
<h2>Name to Select: </h2> 
Flight Number: <input type="text" name="flight_num"/><br> 
<input type="submit" value="Select"/><input type="reset"/> 
</form>

<footer>
        <p>&copy; 2024 TravelX Booking. All rights reserved.</p>
    </footer>
