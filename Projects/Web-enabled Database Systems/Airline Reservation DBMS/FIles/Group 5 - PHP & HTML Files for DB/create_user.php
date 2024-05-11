</head>
<body>
    <header>
        <h1>Welcome to Flight Booking</h1>
    </header>
    <nav>

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
$userId = $_POST['user_id'];
$username = $_POST['username'];
$password = $_POST['password'];

try {
    $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    // Check if the provided user ID already exists
    $checkStmt = $conn->prepare("SELECT user_id FROM user WHERE user_id = :userId");
    $checkStmt->bindParam(':userId', $userId);
    $checkStmt->execute();

    if ($checkStmt->rowCount() > 0) {
        echo "<h2>User ID already exists. Please choose a different ID.</h2>";
    } else {
        // Prepare SQL and bind parameters
        $stmt = $conn->prepare("INSERT INTO user (user_id, user_name, password) VALUES (:userId, :username, :password)");
        $stmt->bindParam(':userId', $userId);
        $stmt->bindParam(':username', $username);
        $stmt->bindParam(':password', $password);

        $stmt->execute();

        if ($stmt->rowCount() > 0) {
            echo "<h2>User created successfully!</h2>";
            echo "User ID: " . $userId . "<br>";
            echo "Username: " . $username . "<br>";
            echo "Password: " . $password . "<br>";
		 echo "<p><a href='index.html'>Return to Home</a></p>";
		echo "<p><a href='search_flights_form.html'>Search Flights</a></p>";

        } else {
            echo "<h2>Failed to create user.</h2>";
        }
    }
} catch(PDOException $e) {
    echo "Error: " . $e->getMessage();
}

$conn = null; // Close connection
?>

<footer>
        <p>&copy; 2024 TravelX Booking. All rights reserved.</p>
    </footer>