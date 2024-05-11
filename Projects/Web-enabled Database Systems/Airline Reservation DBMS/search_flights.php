<?php
// Include the file to make a database connection
include 'db.php';

// Check if either depart_city or arrival_city is not the default value
if (isset($_POST['depart_city']) && $_POST['depart_city'] !== 'default' || isset($_POST['arrival_city']) && $_POST['arrival_city'] !== 'default') {
    $depart_city = isset($_POST['depart_city']) && $_POST['depart_city'] !== 'default' ? $_POST['depart_city'] : null;
    $arrival_city = isset($_POST['arrival_city']) && $_POST['arrival_city'] !== 'default' ? $_POST['arrival_city'] : null;

    try {
        // Set error mode to exception
        $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

        // Query to select flights based on depart_city and arrival_city
        $query = "SELECT * FROM flight WHERE depart_city = :depart_city AND arrival_city = :arrival_city";

        // Prepare the statement
        $stmt = $conn->prepare($query);

        // Bind the parameters
        $stmt->bindParam(':depart_city', $depart_city);
        $stmt->bindParam(':arrival_city', $arrival_city);

        // Execute the query
        $stmt->execute();

        // Check if there are any results
        if ($stmt->rowCount() > 0) {
            // Display the table header
            echo "<h2>Flights from $depart_city to $arrival_city</h2>";
            echo "<table border='1'>";
            echo "<tr><th>Flight Number</th><th>Departure City</th><th>Arrival City</th><th>Departure Date</th><th>Arrival Date</th><th>Departure Time</th><th>Arrival Time</th><th>Price</th></tr>";

            // Fetch and display each row of the result set
            while ($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
                echo "<tr>";
                echo "<td>" . $row['flight_num'] . "</td>";
                echo "<td>" . $row['depart_city'] . "</td>";
                echo "<td>" . $row['arrival_city'] . "</td>";
                echo "<td>" . $row['dep_date'] . "</td>";
                echo "<td>" . $row['arr_date'] . "</td>";
                echo "<td>" . $row['dep_time'] . "</td>";
                echo "<td>" . $row['arr_time'] . "</td>";
                echo "<td>" . $row['price'] . "</td>";
                echo "</tr>";
            }

            echo "</table>";
            echo "<p><a href='ticket_payment.html'>Proceed to Ticket Payment</a></p>";
        } else {
            echo "No flights found from $depart_city to $arrival_city";
        }
    } catch (PDOException $e) {
        echo "Error: " . $e->getMessage();
    }
} else {
    echo "Please select a departing city and an arriving city.";
}

// Close the database connection
$conn = null;
?>